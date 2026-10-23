"""Subnetting drills. The tutor must never state a wrong fact, so answers are
verified against ipaddress rather than trusted."""

import ipaddress
import re

import pytest

from ccnabot.drills.subnetting import (
    Difficulty, Drill, QuestionKind, generate, generate_set,
)

IP = r"(\d+\.\d+\.\d+\.\d+)"
SEEDS = range(400)


def _oracle(drill: Drill) -> str | None:
    """Recompute the answer independently, from the prompt text alone."""
    p, k = drill.prompt, drill.kind
    if k is QuestionKind.NETWORK_ADDRESS:
        a, n = re.search(IP + r"/(\d+)", p).groups()
        return str(ipaddress.IPv4Network(f"{a}/{n}", strict=False).network_address)
    if k is QuestionKind.BROADCAST_ADDRESS:
        a, n = re.search(IP + r"/(\d+)", p).groups()
        return str(ipaddress.IPv4Network(f"{a}/{n}", strict=False).broadcast_address)
    if k is QuestionKind.HOST_COUNT:
        n = int(re.search(r"/(\d+) subnet", p).group(1))
        return str(2 ** (32 - n) - 2)
    if k is QuestionKind.HOST_RANGE:
        a, n = re.search(IP + r"/(\d+)", p).groups()
        net = ipaddress.IPv4Network(f"{a}/{n}", strict=False)
        return f"{net.network_address + 1} {net.broadcast_address - 1}"
    if k is QuestionKind.PREFIX_FOR_HOSTS:
        need = int(re.search(r"needs (\d+)", p).group(1))
        bits = 1
        while 2 ** bits - 2 < need:
            bits += 1
        return f"/{32 - bits}"
    if k is QuestionKind.SAME_SUBNET:
        a, b, n = re.search(IP + r" and " + IP + r" in the same /(\d+)", p).groups()
        net = ipaddress.IPv4Network(f"{a}/{n}", strict=False)
        return "yes" if ipaddress.IPv4Address(b) in net else "no"
    if k is QuestionKind.MASK_CONVERT:
        m = re.search(r"Write /(\d+) in dotted", p)
        if m:
            return str(ipaddress.IPv4Network(f"0.0.0.0/{m.group(1)}").netmask)
        mask = re.search(r"mask " + IP, p).group(1)
        return "/" + str(ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen)
    return None


@pytest.mark.parametrize("kind", list(QuestionKind))
@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_answers_match_an_independent_oracle(kind, difficulty):
    for seed in SEEDS:
        drill = generate(kind, difficulty, seed=seed)
        expected = _oracle(drill)
        assert expected is not None
        assert Drill.normalise(drill.answer) == Drill.normalise(expected), drill.prompt


@pytest.mark.parametrize("kind", list(QuestionKind))
def test_correct_answers_are_accepted(kind):
    for seed in SEEDS:
        drill = generate(kind, Difficulty.WITHIN, seed=seed)
        assert drill.check(drill.answer)[0]
        for form in drill.accepted:
            assert drill.check(form)[0], f"{form!r} rejected for {drill.prompt}"


@pytest.mark.parametrize("kind", list(QuestionKind))
def test_no_distractor_equals_the_answer(kind):
    """A 'wrong' answer that is actually right would tell a correct student
    they had made a mistake."""
    for seed in SEEDS:
        drill = generate(kind, Difficulty.DESIGN, seed=seed)
        valid = {Drill.normalise(drill.answer)} | {Drill.normalise(a) for a in drill.accepted}
        for m in drill.misconceptions:
            assert Drill.normalise(m.value) not in valid, drill.prompt


def test_seeded_generation_is_reproducible():
    """An instructor must be able to reissue the exact same practice set."""
    assert generate(seed=7).prompt == generate(seed=7).prompt
    assert generate(seed=7).prompt != generate(seed=8).prompt


def test_responses_are_forgiving_of_formatting():
    drill = generate(QuestionKind.HOST_RANGE, Difficulty.WITHIN, seed=3)
    first, last = drill.answer.split()
    for variant in (f"  {first}   {last} ", f"{first}-{last}", f"{first} to {last}"):
        assert drill.check(variant)[0], variant


def test_a_miss_names_the_misconception():
    """Diagnosis, not just marking: the tutor should know *which* error."""
    drill = generate(QuestionKind.HOST_COUNT, Difficulty.WITHIN, seed=1)
    total = str(2 ** (32 - int(re.search(r"/(\d+)", drill.prompt).group(1))))
    ok, missed = drill.check(total)
    assert not ok
    assert missed is not None and missed.code == "forgot_minus_two"
    assert "network address" in missed.explanation


def test_broadcast_off_by_one_is_diagnosed():
    drill = generate(QuestionKind.BROADCAST_ADDRESS, Difficulty.WITHIN, seed=5)
    last_host = str(ipaddress.IPv4Address(drill.answer) - 1)
    ok, missed = drill.check(last_host)
    assert not ok and missed is not None and missed.code == "off_by_one"


def test_unrecognised_wrong_answer_is_simply_wrong():
    drill = generate(QuestionKind.NETWORK_ADDRESS, Difficulty.WITHIN, seed=2)
    ok, missed = drill.check("1.2.3.4")
    assert not ok and missed is None


def test_hints_are_progressive_and_do_not_leak_the_answer():
    """Scaffolding means one step at a time. Releasing the solution on the
    first hint removes the productive struggle that does the teaching."""
    drill = generate(QuestionKind.NETWORK_ADDRESS, Difficulty.CROSS, seed=11)
    assert drill.hint_levels >= 3
    assert drill.hint(0) is None
    assert drill.hint(1) != drill.answer
    assert drill.answer not in drill.hint(1)
    assert drill.answer in drill.hint(drill.hint_levels), "final step resolves it"
    assert drill.hint(99) == drill.hint(drill.hint_levels), "clamps, not crashes"


def test_every_drill_carries_a_worked_solution():
    for kind in QuestionKind:
        for seed in range(50):
            drill = generate(kind, Difficulty.WITHIN, seed=seed)
            assert drill.steps, f"{kind} has no scaffold"
            assert drill.task_id == "504"


def test_practice_sets_interleave_question_kinds():
    """Ten broadcast questions in a row lets a student apply one procedure
    repeatedly, which feels like fluency and is not."""
    drills = generate_set(12, seed=4)
    kinds = [d.kind for d in drills]
    assert len(drills) == 12
    assert len(set(kinds)) >= 4
    assert not any(a == b for a, b in zip(kinds, kinds[1:])), "adjacent repeats"


def test_practice_set_respects_a_kind_filter():
    only = [QuestionKind.NETWORK_ADDRESS, QuestionKind.BROADCAST_ADDRESS]
    assert {d.kind for d in generate_set(8, seed=2, kinds=only)} <= set(only)


def test_host_counts_never_teach_a_negative():
    for seed in SEEDS:
        drill = generate(QuestionKind.HOST_COUNT, Difficulty.CROSS, seed=seed)
        assert int(drill.answer) >= 2
