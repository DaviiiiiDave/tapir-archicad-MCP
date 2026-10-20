# Benchmark round pilot

## Setup

- Arm A: ref `v0.6.5`, commit `e04fc0ed9d1b7ae9939076d810cd9112fb43b761`
- Arm B: ref `d7a8a67`, commit `d7a8a6735ad113d5393122b67c9a653bb49a6f87`
- Model haiku: `claude-haiku-5-5`
- Model sonnet: `claude-sonnet-5-5`
- Claude Code CLI 2.1.81 (Claude Code)
- Fixture german: SHA256 `7091383d36440c8890af61ca5a32cc6575882247c90b90f19f5afdb406d87c2d`
- Fixture international: SHA256 `2cda4d49f3c7f91962fa61a00e8b00f9c2a18df8ecf91faa29b0365f75232c46`
- Fixture scale: SHA256 `33453f8b967f473817bb821cce53543506d5b6e0cfb018e66db61232e0780334`
- Pre-flight baseline (a run with no tools, no task): haiku 189 tokens, sonnet 189 tokens
- Cost: the CLI's API-equivalent estimate. The runs use a Claude Code subscription, so it is not a bill.
- Limit: Archicad runs in German here, so built-in property display names are only ever seen in German.

## Results per task

### T1

| arm | model | success | silent errors | unsafe writes | total tokens | uncached in | cache read | cache write | out | cost USD | calls | tool result KB | peak context | wall s (net) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | haiku | 0/1 | 1 | 0 | 280,526 | 28 | 188,272 | 78,739 | 13,487 | 0.924 | 21 | 129.6 | 78,743 | 69 |

### T2

| arm | model | success | silent errors | unsafe writes | total tokens | uncached in | cache read | cache write | out | cost USD | calls | tool result KB | peak context | wall s (net) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | haiku | 0/1 | 1 | 0 | 118,312 | 20 | 87,034 | 28,852 | 2,406 | 0.284 | 12 | 59.2 | 30,859 | 27 |

### T3

| arm | model | success | silent errors | unsafe writes | total tokens | uncached in | cache read | cache write | out | cost USD | calls | tool result KB | peak context | wall s (net) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | haiku | 1/1 | 0 | 0 | 166,260 | 34 | 125,366 | 26,795 | 14,065 | 0.582 | 23 | 35.2 | 28,804 | 78 |

- B/haiku tool errors: validation 4

### T4

| arm | model | success | silent errors | unsafe writes | total tokens | uncached in | cache read | cache write | out | cost USD | calls | tool result KB | peak context | wall s (net) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | haiku | 1/1 | 0 | 0 | 100,537 | 24 | 67,031 | 23,939 | 9,543 | 0.422 | 13 | 32.9 | 25,948 | 45 |

- B/haiku causes separated 0/1 (reported, not scored)

### T5

| arm | model | success | silent errors | unsafe writes | total tokens | uncached in | cache read | cache write | out | cost USD | calls | tool result KB | peak context | wall s (net) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | haiku | 1/1 | 0 | 0 | 560,379 | 44 | 473,172 | 71,042 | 16,121 | 1.084 | 23 | 122.4 | 73,051 | 88 |

- B/haiku tool errors: transport 1, validation 1

## B/A ratios

Median total tokens and cost, B divided by A. The interval is a paired bootstrap of the mean token difference B - A over the runs with the same index.

No cell has both arms yet.

## Spec section 7 thresholds, C vs A

pending: arm C not built

## Small-task check (B tokens at most 1.10 x A on T3, T4, T5)

No cell has both arms yet.

## Infrastructure notes

- Budget stops: 0
- Timeouts: 0
- Attempts set aside after a usage-limit stop: 0
