#!/bin/sh

SCRIPT="${0##*/}"
SCRIPT_TOP=$(cd "${0%/*}"; pwd)
plural() {
    if [ "$1" -ne 1 ]
    then
        printf "s"
    fi
}
START="$(date +%s.%N)"
report() {
    NOW="$(date +%s.%N)"
    ELAPSED="$(echo "scale=9; $NOW - $START" | bc)"
    case "$ELAPSED" in
        (.*) ELAPSED="0$ELAPSED";;
    esac
    printf "[% 14s] %s\n" "$ELAPSED" "$1"
}
warn() {
    report "$SCRIPT: $1" >&2
}
fail() {
    warn "$1"
    exit ${2-1}
}

cleanup() {
    ec="$?"

    [ -f "$tags" ] && rm "$tags"
    [ -f "$selected" ] && rm "$selected"
    [ -d "$headers" ] && rm -r "$headers"

    report "restoring repo to $INITIAL_SHA..."
    (cd "$REPO" && git checkout -f "$INITIAL_SHA" >/dev/null 2>&1)
    report "restored"

    return "$ec"
}
trap 'cleanup' EXIT

# defaults for development
: "${REPO=/home/mgreenbe/linux}"
: "${OUTDIR=/home/mgreenbe/syscallyp/out}"

# collect all tags
cd "$REPO" || fail "could not cd to $REPO"

# save where we started, so we can leave things in a nice state
INITIAL_BRANCH="$(git branch --show-current)"
if [ "$INITIAL_BRANCH" ]
then
    INITIAL_SHA="$INITIAL_BRANCH"
else
    INITIAL_SHA="$(git rev-parse HEAD)"
    # try to detect tag and use that name, if there's a unique one
    INITIAL_TAG="$(git tag --points-at "$INITIAL_SHA")"
    if [ $(echo "$INITIAL_TAG" | wc -w) -eq 1 ]
    then
        INITIAL_SHA="$INITIAL_TAG"
    fi
fi

# pick a switch (if not given)
tags="$(mktemp)"
selected="$(mktemp)"
# skip v2.6.11, which is a not a proper commit
git tag | grep -E '^v[0-9]+\.[0-9]+(\.[0-9]+)?$' | grep -v -e 'v2.6.11' >"$tags"

if [ "$#" -ge 1 ]
then
    for tag in "$@"
    do
        if grep -q -e "^$tag$" "$tags"
        then
            echo "$tag" >>"$selected"
        elif grep -q -e "^v$tag$" "$tags"
        then
            echo "v$tag" >>"$selected"
        else
            warn "could not find tag '$tag', skipping"
        fi
    done
else
    # select all tags
    cp "$tags" "$selected"
fi

# for each selected tag...
while read tag
do
    report "$tag checkout..."
    git checkout -f "$tag" >/dev/null 2>&1 || fail "could not switch to '$tag'"
    report "$tag checkout complete"

    TAGDIR="$OUTDIR/$tag"
    [ -d "$TAGDIR" ] || mkdir -p "$TAGDIR"

    # identify all architectures
    arches="$TAGDIR/arches"
    cd "$REPO/arch" || fail "could not find 'arch' directory for '$tag'"
    find . -mindepth 1 -maxdepth 1 -type d | cut -d/ -f2 >"$arches"

    # collect syscalls per architecture
    cd "$REPO"
    while read arch
    do
        case "$arch" in
            (arch|um)
                # arch: generated code glitch
                # um: user-mode linux
                continue;;
        esac

        report "$tag $arch"

        report "$tag $arch build..."
        headers="$(mktemp -d)"
        # generate headers (can be complicated on, e.g., arm)
        {
            failures=0
            make mrproper ARCH="$arch" || : $((failures += 1))
            make alldefconfig ARCH="$arch"  || : $((failures += 1))
            make headers_install INSTALL_HDR_PATH="$headers" ARCH="$arch"  || : $((failures += 1))
        } >"$TAGDIR/$arch.log" 2>&1
        if [ "$failures" -ne 0 ]
        then
            report "tag $arch build had $failures error$(plural $failures); see $TAGDIR/$arch.log"
        fi
        report "$tag $arch build complete (in $headers)"

        # identify variants
        grep -e '#if' "$headers"/include/asm/unistd.h >"$TAGDIR/$arch.variants"
        variants=""
        while read variant
        do
            case "$variant" in
                # mandatory variants
                ("#if _MIPS_SIM =="*) variants="$variants _MIPS_SIM=${variant##*= }";;

                # binary variants
                ("#if defined(__thumb__) || defined(__ARM_EABI__)") variants="$variants __ARM_OABI__ DEFAULT";;
                ("#ifndef __powerpc64__") variants="$variants __powerpc64__ DEFAULT";;
                ("#ifdef __s390x__") variants="$variants __s390x__ DEFAULT";;
                ("#ifdef __arch64__") variants="$variants __arch64__ DEFAULT";;
                ("#ifdef __LP64__"|"#if defined(__LP64__) && !defined(__SYSCALL_COMPAT)")
                    variants="$variants __LP64__ DEFAULT";;

                # ignored variants
                ("#ifndef __NR_riscv_flush_icache");;
                ("#ifndef __32bit_syscall_numbers__");;
                ("#ifndef __arch64__");;

                # double-include guards
                ("#ifndef _UAPI_"*"_UNISTD_H");;
                ("#ifndef _"*"_UNISTD_H");;
                ("#if !defined(_UAPI_ASM_ARC_UNISTD_H) || defined(__SYSCALL)");;
                ("#ifndef _ASM_"*"_UNISTD_H_");;
                ("#ifndef _ASM_"*"_UNISTD_H");;

                # warnings on new variants
                (*) warn "unknown $arch variant in $tag: $variant";;
            esac
        done <"$TAGDIR/$arch.variants"

        num_variants="$(echo "$variants" | wc -w)"
        report "$tag $arch has $num_variants variant$(plural $num_variants)"
        if [ "$variants" ]
        then
            for variant in $variants
            do
                # in case there are somehow duplicates...
                [ -e "$arch.$variant" ] && continue
                report "$tag $arch $variant extraction..."
                gcc -I "$headers"/include -D"$variant" -E -dM "$headers"/include/asm/unistd.h |
                    grep -e '#define __NR' |
                    sort -nr -k3 >"$TAGDIR/$arch.$variant"
                "$SCRIPT_TOP"/extract_syscall_numbers.py "$tag" "$arch.$variant" "$TAGDIR/$arch.$variant" >>"$TAGDIR/$arch.$variant.csv"
                report "$tag $arch $variant extraction complete"
            done
        else
            report "$tag $arch extraction..."
            # grab syscall defines
            gcc -I "$headers"/include -E -dM "$headers"/include/asm/unistd.h |
                grep -e '#define __NR' |
                sort -nr -k3 >"$TAGDIR/$arch"
            "$SCRIPT_TOP"/extract_syscall_numbers.py "$tag" "$arch" "$TAGDIR/$arch" >>"$TAGDIR/$arch.csv"
            report "$tag $arch extraction complete"
        fi

        #rm -r "$headers"
        unset headers

        cat "$TAGDIR"/*.csv >"$OUTDIR"/$tag.csv
        report "$tag $arch complete"
    done <"$arches"

    report "$tag complete"
done <"$selected"
