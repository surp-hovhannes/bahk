#!/bin/bash
cd /Users/oshii/clawd/fast-and-pray-pm-agent/code/bahk

findings=(
fnd_sig-feat-route-90720ae5ed-a2b225_660f9a7afb
fnd_sig-feat-route-90cc5317f5-55ffdf_bedfbc4130
fnd_sig-feat-route-9ae098f9dd-c4e967_295190eb56
fnd_sig-feat-route-9b9cb847ff-a80d5f_bdc6ad803f
fnd_sig-feat-route-a9f583a359-2a272e_8c7121dd00
fnd_sig-feat-route-ae5b368dfc-dc8372_a503dcea07
fnd_sig-feat-route-ae90d4891a-cea2c6_a9a66a59c7
fnd_sig-feat-route-b21f89cfc5-b73504_80f1933f68
fnd_sig-feat-route-b25a366708-0aa7d0_d9a3f6c29f
fnd_sig-feat-route-bc0a0da836-925d49_9fdb4fd492
fnd_sig-feat-route-d17e1cae13-c8d65f_85f0ba2dc3
fnd_sig-feat-route-d5ba6f2ad2-7fd0ff_51e32ba857
fnd_sig-feat-route-e348962fc3-17c296_775de2446a
fnd_sig-feat-route-eb9b5f4c42-c8b30d_05b3e7049c
fnd_sig-feat-route-f2b0b536dc-6ca874_ab403dcade
fnd_sig-feat-route-f7f2d12b9e-3861da_62447d877c
fnd_sig-feat-test-suite-98dd959b96-a_b99e2cf38a
fnd_sig-feat-test-suite-d44252114d-3_b2bca9f67f
fnd_sig-feat-test-suite-d44252114d-f_1abc310ab4
)

results=()

for i in "${!findings[@]}"; do
    id="${findings[$i]}"
    num=$((i+1))

    echo ""
    echo "========================================"
    echo "[$num/19] $id"
    echo "========================================"

    # Reset branch and clean to pristine state
    git checkout test-hardening-hub-pt2 --force >/dev/null 2>&1
    git reset HEAD >/dev/null 2>&1
    git checkout -- . >/dev/null 2>&1
    git clean -fd >/dev/null 2>&1

    # Fresh .clawpatch for each iteration
    rm -rf .clawpatch
    clawpatch init --force >/dev/null 2>&1
    clawpatch map --quiet >/dev/null 2>&1

    # Try fix (may fail - that's OK)
    echo "--- FIX ---"
    fix_out=$(clawpatch fix --finding "$id" 2>&1) || true
    fix_exit=${PIPESTATUS[0]}
    if echo "$fix_out" | grep -qo "dirty worktree"; then
        echo "FIX: SKIPPED (dirty worktree)"
        fix_exit=3
    elif echo "$fix_out" | grep -q "error:"; then
        echo "FIX: ERROR"
        fix_exit=1
    elif echo "$fix_out" | grep -q "validation failed"; then
        echo "FIX: VALIDATION_FAILED (exit 6)"
        fix_exit=6
    else
        echo "FIX: APPLIED (exit 0)"
        fix_exit=0
    fi

    # Always run revalidate
    echo "--- REVALIDATE ---"
    rev_out=$(clawpatch revalidate --finding "$id" 2>&1) || true
    if echo "$rev_out" | grep -qo "outcome=fixed\|fixed=1"; then
        outcome="fixed"
    elif echo "$rev_out" | grep -qo "outcome=open\|open=1"; then
        outcome="open"
    elif echo "$rev_out" | grep -qo "outcome=falsePositive"; then
        outcome="falsePositive"
    elif echo "$rev_out" | grep -qo "outcome=uncertain"; then
        outcome="uncertain"
    else
        outcome="unknown"
    fi
    echo "OUTCOME: $outcome"

    echo "$num|$id|fix=$fix_exit|outcome=$outcome"
    results+=("$num|$id|fix=$fix_exit|outcome=$outcome")

    # Clean up for next iteration
    git reset HEAD >/dev/null 2>&1
    git checkout -- . >/dev/null 2>&1
    git clean -fd >/dev/null 2>&1

    # Verify .clawpatch.bak for next iteration is gone
    rm -rf ".clawpatch.bak" ".clawpatch.bak2"

    sleep 1
done

echo ""
echo "=== FINAL SUMMARY ==="
printf '%s\n' "${results[@]}"
echo ""
echo "Total: ${#results[@]} findings processed"
