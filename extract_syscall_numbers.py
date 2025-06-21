#!/usr/bin/env python3

import os
import re
import sys

DEFINE_SYSCALL_REGEX = re.compile(r"#define\s+(?P<syscall>__NR(?:3264)?_[a-zA-Z0-9_]+)\s+(?P<number>.*)")
SYSCALL_REF_REGEX = re.compile(r"(?P<refcall>__NR(?:3264)?_[a-zA-Z0-9_]+)(?:\s+\+\s+(?P<increment>\d+))?")
NON_SYSCALLS = ['arch_specific_syscall', 'Linux', 'SYSCALL_MASK', 'SYSCALL_BASE', 'OABI_SYSCALL_BASE']

def try_int(number: str | None) -> int | None:
    if number is None:
        return None
    elif number[0:2] == '0x':
        base = 16
    elif number[0:1] == '0':
        # just fine if number == '0'
        base = 8
    else:
        base = 10

    try:
        return int(number, base=base)
    except ValueError:
        return None

class defines:
    def __init__(self, tag: str, arch: str):
        self.tag = tag
        self.arch = arch
        self.syscall_to_nr = dict()
        self.pending = dict()

    def map(self, syscall: str, nr: int):
        self.syscall_to_nr[syscall] = nr

    def is_mapped(self, syscall: str):
        return syscall in self.syscall_to_nr

    def mark_pending(self, syscall: str, increment: int | None, refcall: str):
        self.pending.setdefault(refcall, list()).append((syscall, increment))

    def process(self, line: str):
        line = line.rstrip()
        if m := DEFINE_SYSCALL_REGEX.match(line):
            syscall = m.group('syscall')
            number = m.group('number')

            if syscall in self.syscall_to_nr:
                print(f'WARNING: {syscall} was defined as {self.syscall_to_nr[syscall]}, redefined as {number}', file=sys.stderr)

            # strip parens, if present
            if number[0] == '(' and number[-1] == ')':
                number = number[1:-1]

            if (nr := try_int(number)) is not None:
                # #define _NR_foo n
                self.map(syscall, nr)
            elif m := SYSCALL_REF_REGEX.fullmatch(number):
                # #define _NR_foo __NR_bar
                # #define _NR_foo (__NR_bar + n)
                refcall = m.group('refcall')
                increment = try_int(m.group('increment')) or 0

                if not self.is_mapped(refcall):
                    self.mark_pending(syscall, increment, refcall)
                    return

                nr = self.syscall_to_nr[refcall] + increment
                self.map(syscall, nr)
            else:
                # not a raw reference
                raise ValueError(f'could not process "{number}"')

            # were there any pending calls on this syscall? resolve them now
            # syscall, nr are current call
            #
            # we had:
            #   #define __NR_depcall (__NR_syscall + increment)
            # so:
            #   depcall = nr + increment
            keys = [syscall]
            while len(keys) > 0:
                key = keys.pop()
                for (depcall, increment) in self.pending.pop(key, []):
                    self.map(depcall, nr + increment)
                    keys.append(depcall) # transitively resolve other calls

    def show(self):
        for (syscall, nr) in self.syscall_to_nr.items():
            if syscall not in NON_SYSCALLS:
                print(f'{self.tag},{self.arch},{syscall},{nr}')

        if len(defs.pending) > 0:
            print('There were undefined symbols:', file=sys.stderr)
            for (key, deps) in defs.pending.items():
                print(f'  {key} had {len(deps)} definitions pending', file=sys.stderr)
                for (dep, dep3264, depincr) in deps:
                    print(f'    {dep}{dep3264 or ""} = {key}{"" if depincr == 0 else f" + {depincr}"}')

if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(f'Usage: {os.path.basename(sys.argv[0])} TAG ARCH DEFINE_FILE')
        sys.exit(2)
    defs = defines(sys.argv[1], sys.argv[2])

    with open(sys.argv[3]) as define_file:
        for line in define_file:
            try:
                defs.process(line)
            except ValueError as e:
                print(f'{sys.argv[1]} {sys.argv[2]}: extraction error in {sys.argv[3]}: {e}', file=sys.stderr)

    defs.show()