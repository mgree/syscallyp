#!/usr/bin/env python3

import os
import re
import sys

DEFINE_SYSCALL_REGEX = re.compile(r"#define\s+__NR(?P<nr3264>3264)?_(?P<syscall>[a-zA-Z0-9_]+)\s+(?P<number>.*)")
SYSCALL_REGEX = re.compile(r"__NR(?P<nr3264>3264)?_(?P<syscall>[a-zA-Z0-9_]+)")
SYSCALL_ARITH_REGEX = re.compile(r"__NR(?P<nr3264>3264)?_(?P<syscall>[a-zA-Z0-9_]+)\s+\+\s+(?P<increment>\d+)")
NON_SYSCALLS = ['arch_specific_syscall', 'Linux', 'SYSCALL_MASK', 'SYSCALL_BASE', 'OABI_SYSCALL_BASE']

class defines:
    def __init__(self, tag: str, arch: str):
        self.tag = tag
        self.arch = arch
        self.defines = dict()
        self.defines3264 = dict()
        self.pending = dict()

    def process(self, line: str):
        line = line.rstrip()
        if m := DEFINE_SYSCALL_REGEX.match(line):
            syscall = m.group('syscall')
            number = m.group('number')

            nr3264 = m.group('nr3264') or ''
            defines = self.defines3264 if nr3264 else self.defines

            if syscall in defines:
                print(f'WARNING: {syscall} was defined as {defines[syscall]}, redefined as {number}', file=sys.stderr)

            try:
                # #define __NR_foo n
                if number[0:2] == '0x':
                    base = 16
                elif number[0] == '0':
                    base = 8
                else:
                    base = 10
                value = int(number, base=base)
                defines[syscall] = number
            except ValueError:
                # strip parens, if present
                if number[0] == '(' and number[-1] == ')':
                    number = number[1:-1]

                if m := SYSCALL_REGEX.fullmatch(number):
                    # #define __NR_foo __NR_bar
                    ref3264 = m.group('nr3264')
                    refcall = m.group('syscall')
                    reference = self.defines3264 if ref3264 else self.defines
                    if refcall not in reference:
                        self.pending.setdefault(f'{refcall}{ref3264 or ""}', list()).append((nr3264, syscall, 0))
                        return

                    value = reference[refcall]
                elif m := SYSCALL_ARITH_REGEX.fullmatch(number):
                    # #define _NR_foo (__NR_bar + n)
                    ref3264 = m.group('nr3264')
                    refcall = m.group('syscall')
                    reference = self.defines3264 if ref3264 else self.defines
                    increment = m.group('increment')
                    try:
                        if increment[0:2] == '0x':
                            base = 16
                        elif increment[0] == '0':
                            base = 8
                        else:
                            base = 10

                        increment = int(increment, base=base)
                    except ValueError:
                        raise ValueError(f'could not process {number}')

                    if refcall not in reference:
                        self.pending.setdefault(f'{refcall}{ref3264 or ""}', list()).append((nr3264, syscall, increment))
                        return

                    value = reference[refcall] + increment
                else:
                    # not a raw reference
                    raise ValueError(f'could not process {number}')

            defines[syscall] = value

            # were there any pending calls on this syscall? resolve them now
            keys = [f'{syscall}{nr3264 or ""}']
            while len(keys) != 0:
                key = keys.pop()
                for (dep3264, depcall, increment) in self.pending.pop(key, []):
                    keys.append(f'{depcall}{dep3264 or ""}')
                    dep = self.defines3264 if dep3264 else self.defines
                    dep[depcall] = value + increment

    def show(self):
        for (syscall, nr) in self.defines.items():
            if syscall not in NON_SYSCALLS:
                print(f'{self.tag},{self.arch},{syscall},{nr}')

        for (syscall, nr) in self.defines3264.items():
            if syscall not in self.defines and syscall not in NON_SYSCALLS:
                print(f'{self.tag},{self.arch},{syscall},{nr}')

if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(f'Usage: {os.path.basename(sys.argv[0])} TAG ARCH DEFINE_FILE')
        sys.exit(2)
    defines = defines(sys.argv[1], sys.argv[2])

    with open(sys.argv[3]) as define_file:
        for line in define_file:
            try:
                defines.process(line)
            except ValueError as e:
                print(e)

    defines.show()

    if len(defines.pending) > 0:
        print('There were undefined symbols:')
        for (key, deps) in defines.pending.items():
            print(f'  {key} had {len(deps)} definitions pending')
            for (dep, dep3264, depincr) in deps:
                print(f'    {dep}{dep3264 or ""} = {key}{"" if depincr == 0 else f" + {depincr}"}')