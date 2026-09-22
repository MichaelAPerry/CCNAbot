"""Generated subnetting drills for CIP task 504.

504 is the one task on the grid that is procedural, endlessly generatable and
exactly checkable, so it needs no device, no instructor and no language model
to assess. It is also where students reliably struggle, which makes it the
cheapest real win available.

Three things make this a teaching tool rather than a quiz:

* **Answers come from `ipaddress`**, never from hand-rolled bit arithmetic and
  never from a language model. The rule engine is authoritative; the model, if
  one is involved at all, only phrases things.

* **Every drill carries a worked solution** as ordered steps, so the tutor can
  release one hint at a time instead of surrendering the answer. Productive
  struggle is the point; rescuing a student too early removes the learning.

* **Wrong answers are diagnosed, not just marked.** Each drill knows the
  specific errors students make -- forgetting to subtract network and
  broadcast, off-by-one on the broadcast address, applying the block size to
  the wrong octet -- so a miss identifies the misconception instead of
  reporting "incorrect".
"""

from __future__ import annotations

import enum
import ipaddress
import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

IPv4Network = ipaddress.IPv4Network
IPv4Address = ipaddress.IPv4Address


class Difficulty(enum.IntEnum):
    """Ordered so the tutor can advance a student through boundaries."""

    OCTET = 1    # /8, /16, /24 -- clean octet boundaries
    WITHIN = 2   # /25../30 -- subnetting inside the final octet
    CROSS = 3    # /17../23 -- borrowing across an octet boundary
    DESIGN = 4   # size a subnet for a required host count


class QuestionKind(enum.Enum):
    NETWORK_ADDRESS = "network_address"
    BROADCAST_ADDRESS = "broadcast_address"
    HOST_RANGE = "host_range"
    HOST_COUNT = "host_count"
    PREFIX_FOR_HOSTS = "prefix_for_hosts"
    SAME_SUBNET = "same_subnet"
    MASK_CONVERT = "mask_convert"


@dataclass(frozen=True, slots=True)
class Misconception:
    """A wrong answer worth recognising, and what it means."""

    code: str
    value: str
    explanation: str


@dataclass(frozen=True, slots=True)
class Drill:
    """One generated question, its answer, its scaffold and its diagnostics."""

    kind: QuestionKind
    difficulty: Difficulty
    task_id: str
    prompt: str
    answer: str
    accepted: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    misconceptions: tuple[Misconception, ...] = ()
    seed: int | None = None

    def __post_init__(self) -> None:
        """Drop degenerate distractors.

        A generator can produce a "wrong" answer that happens to equal the
        right one -- sizing for 120 hosts gives /25 whether or not you
        remember to subtract the network and broadcast addresses. Marking
        such a response as a misconception would tell a student who answered
        correctly that they had erred.
        """
        valid = {self.normalise(self.answer)} | {self.normalise(a) for a in self.accepted}
        kept, seen = [], set(valid)
        for m in self.misconceptions:
            key = self.normalise(m.value)
            # Also drop duplicates: two distractors with the same value would
            # report one misconception's explanation for the other's error.
            if key in seen:
                continue
            seen.add(key)
            kept.append(m)
        if len(kept) != len(self.misconceptions):
            object.__setattr__(self, "misconceptions", tuple(kept))

    @staticmethod
    def normalise(text: str) -> str:
        return " ".join(str(text).strip().lower().replace(",", " ").split())

    def check(self, response: str) -> tuple[bool, Misconception | None]:
        """Grade a response, and name the misconception when one fits."""
        given = self.normalise(response)
        if given in {self.normalise(a) for a in (self.answer, *self.accepted)}:
            return True, None
        for m in self.misconceptions:
            if given == self.normalise(m.value):
                return False, m
        return False, None

    def hint(self, level: int) -> str | None:
        """Progressive disclosure: one step at a time, never the whole solution."""
        if level < 1 or not self.steps:
            return None
        return self.steps[min(level, len(self.steps)) - 1]

    @property
    def hint_levels(self) -> int:
        return len(self.steps)


# -- helpers -------------------------------------------------------------

def _mask_octets(prefix: int) -> list[int]:
    return [int(o) for o in str(IPv4Network(f"0.0.0.0/{prefix}").netmask).split(".")]


def _block_octet(prefix: int) -> tuple[int, int]:
    """(index of the octet the block size applies to, block size)."""
    idx = min(prefix // 8, 3)
    return idx, 256 - _mask_octets(prefix)[idx]


def _usable(net: IPv4Network) -> int:
    return max(net.num_addresses - 2, 0)


def _steps_for_boundary(addr: IPv4Address, prefix: int, net: IPv4Network) -> tuple[str, ...]:
    mask = IPv4Network(f"0.0.0.0/{prefix}").netmask
    idx, block = _block_octet(prefix)
    octets = [int(o) for o in str(addr).split(".")]
    ordinal = ("first", "second", "third", "fourth")[idx]
    if block == 1:
        return (
            f"A /{prefix} mask is {mask}.",
            f"The mask ends on an octet boundary, so the {ordinal} octet is already the network value.",
            f"Network address = {net.network_address}",
        )
    multiple = (octets[idx] // block) * block
    return (
        f"A /{prefix} mask is {mask}.",
        f"Work in the {ordinal} octet: block size = 256 - {_mask_octets(prefix)[idx]} = {block}.",
        f"Subnets step by {block}: 0, {block}, {2 * block}, ...",
        f"{octets[idx]} falls in the block starting at {octets[idx] // block} x {block} = {multiple}.",
        f"Network address = {net.network_address}",
    )


# -- generators ----------------------------------------------------------

def _random_network(rng: random.Random, difficulty: Difficulty) -> tuple[IPv4Address, int]:
    if difficulty is Difficulty.OCTET:
        prefix = rng.choice([8, 16, 24])
    elif difficulty is Difficulty.WITHIN:
        prefix = rng.randint(25, 30)
    else:
        prefix = rng.randint(17, 23)
    base = rng.choice([10, 172, 192, rng.randint(1, 223)])
    if base == 172:
        octets = [172, rng.randint(16, 31), rng.randint(0, 255), rng.randint(1, 254)]
    elif base == 192:
        octets = [192, 168, rng.randint(0, 255), rng.randint(1, 254)]
    else:
        octets = [base, rng.randint(0, 255), rng.randint(0, 255), rng.randint(1, 254)]
    return IPv4Address(".".join(str(o) for o in octets)), prefix


def _gen_network_address(rng: random.Random, d: Difficulty) -> Drill:
    addr, prefix = _random_network(rng, d)
    net = IPv4Network(f"{addr}/{prefix}", strict=False)
    try:
        next_block = str(net.broadcast_address + 1)
    except ipaddress.AddressValueError:  # last subnet in the space
        next_block = str(net.network_address)

    return Drill(
        kind=QuestionKind.NETWORK_ADDRESS,
        difficulty=d,
        task_id="504",
        prompt=f"What is the network address of {addr}/{prefix}?",
        answer=str(net.network_address),
        accepted=(f"{net.network_address}/{prefix}",),
        steps=_steps_for_boundary(addr, prefix, net),
        misconceptions=(
            Misconception(
                "next_block",
                next_block,
                "That is the *next* subnet's network address. The address belongs to the "
                "block that starts at or below its octet value, not the one above it.",
            ),
            Misconception(
                "broadcast_for_network",
                str(net.broadcast_address),
                "That is the broadcast address. The network address is the first address "
                "in the block, the broadcast is the last.",
            ),
        ),
        seed=rng.getrandbits(32),
    )


def _gen_broadcast(rng: random.Random, d: Difficulty) -> Drill:
    addr, prefix = _random_network(rng, d)
    net = IPv4Network(f"{addr}/{prefix}", strict=False)
    idx, block = _block_octet(prefix)
    last_host = net.broadcast_address - 1

    return Drill(
        kind=QuestionKind.BROADCAST_ADDRESS,
        difficulty=d,
        task_id="504",
        prompt=f"What is the broadcast address of {addr}/{prefix}?",
        answer=str(net.broadcast_address),
        steps=(
            *_steps_for_boundary(addr, prefix, net)[:-1],
            f"The block starting at {net.network_address} holds {net.num_addresses} addresses.",
            f"Broadcast = the last address in that block = {net.broadcast_address}",
        ),
        misconceptions=(
            Misconception(
                "off_by_one",
                str(last_host),
                "That is the last *usable host*, one below the broadcast. The broadcast is "
                "the final address in the block.",
            ),
            Misconception(
                "network_for_broadcast",
                str(net.network_address),
                "That is the network address -- the first address in the block, not the last.",
            ),
        ),
        seed=rng.getrandbits(32),
    )


def _gen_host_count(rng: random.Random, d: Difficulty) -> Drill:
    d = Difficulty.WITHIN if d is Difficulty.OCTET else d
    addr, prefix = _random_network(rng, d)
    net = IPv4Network(f"{addr}/{prefix}", strict=False)
    bits = 32 - prefix

    return Drill(
        kind=QuestionKind.HOST_COUNT,
        difficulty=d,
        task_id="504",
        prompt=f"How many usable host addresses does a /{prefix} subnet provide?",
        answer=str(_usable(net)),
        steps=(
            f"A /{prefix} leaves 32 - {prefix} = {bits} host bits.",
            f"That is 2^{bits} = {net.num_addresses} total addresses.",
            "Subtract two: one network address, one broadcast address.",
            f"Usable hosts = {net.num_addresses} - 2 = {_usable(net)}",
        ),
        misconceptions=(
            Misconception(
                "forgot_minus_two",
                str(net.num_addresses),
                "That is the total address count. Two are not assignable to hosts: the "
                "network address and the broadcast address.",
            ),
            Misconception(
                "minus_one",
                str(net.num_addresses - 1),
                "Only one was subtracted. Both the network and the broadcast address are "
                "unusable, so subtract two.",
            ),
        ),
        seed=rng.getrandbits(32),
    )


def _gen_prefix_for_hosts(rng: random.Random, d: Difficulty) -> Drill:
    # A mix of round requirements and values just above a power of two. The
    # latter are where forgetting the two reserved addresses changes the
    # answer (255 hosts needs /23, not /24), which is the case worth drilling.
    needed = rng.choice(
        [2, 5, 10, 14, 25, 30, 50, 60, 100, 120, 200, 300, 500, 1000,
         127, 129, 255, 257, 511, 513, 1025]
    )
    bits = 1
    while (2 ** bits) - 2 < needed:
        bits += 1
    prefix = 32 - bits

    return Drill(
        kind=QuestionKind.PREFIX_FOR_HOSTS,
        difficulty=Difficulty.DESIGN,
        task_id="504",
        prompt=(
            f"A VLAN needs {needed} usable host addresses. What is the smallest "
            f"subnet (prefix length) that fits, written as /n?"
        ),
        answer=f"/{prefix}",
        accepted=(str(prefix), str(IPv4Network(f'0.0.0.0/{prefix}').netmask)),
        steps=(
            f"Find the smallest h where 2^h - 2 >= {needed}.",
            f"2^{bits} - 2 = {(2 ** bits) - 2}, which is the first value that fits.",
            f"So {bits} host bits are needed.",
            f"Prefix = 32 - {bits} = /{prefix}",
        ),
        misconceptions=(
            Misconception(
                "ignored_overhead",
                f"/{32 - max(1, (needed - 1).bit_length())}",
                f"That sizes for {needed} addresses, not {needed} *usable hosts*. "
                "Two addresses in every subnet are reserved.",
            ),
            Misconception(
                "off_by_one_prefix",
                f"/{prefix + 1}",
                f"A /{prefix + 1} gives only {2 ** (bits - 1) - 2} usable hosts, short of "
                f"the {needed} required.",
            ),
        ),
        seed=rng.getrandbits(32),
    )


def _gen_host_range(rng: random.Random, d: Difficulty) -> Drill:
    d = Difficulty.WITHIN if d is Difficulty.OCTET else d
    addr, prefix = _random_network(rng, d)
    net = IPv4Network(f"{addr}/{prefix}", strict=False)
    first, last = net.network_address + 1, net.broadcast_address - 1

    return Drill(
        kind=QuestionKind.HOST_RANGE,
        difficulty=d,
        task_id="504",
        prompt=f"Give the usable host range for {addr}/{prefix} as 'first last'.",
        answer=f"{first} {last}",
        accepted=(f"{first}-{last}", f"{first} - {last}", f"{first} to {last}"),
        steps=(
            *_steps_for_boundary(addr, prefix, net)[:-1],
            f"Network address = {net.network_address}, broadcast = {net.broadcast_address}.",
            f"Usable hosts sit between them: {first} to {last}",
        ),
        misconceptions=(
            Misconception(
                "included_reserved",
                f"{net.network_address} {net.broadcast_address}",
                "That range includes the network and broadcast addresses, neither of which "
                "can be assigned to a host.",
            ),
        ),
        seed=rng.getrandbits(32),
    )


def _gen_same_subnet(rng: random.Random, d: Difficulty) -> Drill:
    addr, prefix = _random_network(rng, d if d is not Difficulty.OCTET else Difficulty.WITHIN)
    net = IPv4Network(f"{addr}/{prefix}", strict=False)
    same = rng.random() < 0.5
    if same and net.num_addresses > 2:
        other = net.network_address + rng.randint(1, net.num_addresses - 2)
    else:
        other = net.broadcast_address + rng.randint(1, max(1, net.num_addresses // 2))
        same = other in net

    return Drill(
        kind=QuestionKind.SAME_SUBNET,
        difficulty=d,
        task_id="504",
        prompt=f"Are {addr} and {other} in the same /{prefix} subnet? Answer yes or no.",
        answer="yes" if same else "no",
        accepted=("y",) if same else ("n",),
        steps=(
            f"Find each address's network with a /{prefix} mask.",
            f"{addr}/{prefix} -> {net.network_address}",
            f"{other}/{prefix} -> {IPv4Network(f'{other}/{prefix}', strict=False).network_address}",
            "Same network address means same subnet."
            if same
            else "Different network addresses, so they are on different subnets.",
        ),
        seed=rng.getrandbits(32),
    )


def _gen_mask_convert(rng: random.Random, d: Difficulty) -> Drill:
    prefix = rng.randint(8, 30)
    mask = IPv4Network(f"0.0.0.0/{prefix}").netmask
    to_dotted = rng.random() < 0.5

    if to_dotted:
        prompt = f"Write /{prefix} in dotted-decimal notation."
        answer, accepted = str(mask), ()
    else:
        prompt = f"Write the mask {mask} in CIDR (/n) notation."
        answer, accepted = f"/{prefix}", (str(prefix),)

    return Drill(
        kind=QuestionKind.MASK_CONVERT,
        difficulty=Difficulty.OCTET if prefix % 8 == 0 else Difficulty.WITHIN,
        task_id="504",
        prompt=prompt,
        answer=answer,
        accepted=accepted,
        steps=(
            f"/{prefix} means {prefix} leading 1 bits.",
            f"That fills {prefix // 8} whole octet(s) with 255"
            + (f", leaving {prefix % 8} bit(s) in the next." if prefix % 8 else "."),
            f"/{prefix} = {mask}",
        ),
        seed=rng.getrandbits(32),
    )


_GENERATORS: dict[QuestionKind, Callable[[random.Random, Difficulty], Drill]] = {
    QuestionKind.NETWORK_ADDRESS: _gen_network_address,
    QuestionKind.BROADCAST_ADDRESS: _gen_broadcast,
    QuestionKind.HOST_COUNT: _gen_host_count,
    QuestionKind.HOST_RANGE: _gen_host_range,
    QuestionKind.PREFIX_FOR_HOSTS: _gen_prefix_for_hosts,
    QuestionKind.SAME_SUBNET: _gen_same_subnet,
    QuestionKind.MASK_CONVERT: _gen_mask_convert,
}


def generate(
    kind: QuestionKind | None = None,
    difficulty: Difficulty = Difficulty.WITHIN,
    seed: int | None = None,
) -> Drill:
    """Produce one drill. Passing `seed` makes it exactly reproducible."""
    rng = random.Random(seed)
    if kind is None:
        kind = rng.choice(list(_GENERATORS))
    return _GENERATORS[kind](rng, difficulty)


def generate_set(
    count: int,
    difficulty: Difficulty = Difficulty.WITHIN,
    seed: int | None = None,
    kinds: Sequence[QuestionKind] | None = None,
) -> list[Drill]:
    """A mixed practice set.

    Question kinds are interleaved rather than blocked: a run of ten broadcast
    questions lets a student apply one procedure ten times, which feels like
    fluency and is not. Mixing forces them to identify what is being asked.
    """
    rng = random.Random(seed)
    pool = list(kinds or _GENERATORS)
    out: list[Drill] = []
    last: QuestionKind | None = None
    for _ in range(count):
        choices = [k for k in pool if k is not last] or pool
        kind = rng.choice(choices)
        out.append(_GENERATORS[kind](rng, difficulty))
        last = kind
    return out
