import cocotb
from cocotb.triggers import Timer


def _expect(s):
    """Reference model of the decoder contract for a 5-bit sample vector s[0..4].

    Algorithmic (not a hand table): sort-check for monotonicity, then count the
    leading run of bits[0] to get fine_phase. samples[0] is the earliest sample
    (previous period's delayed phase-270 tap); samples[4] is the latest (this
    period's phase-270 tap).
    """
    bits = [(s >> i) & 1 for i in range(5)]            # bits[0]=phase0 (earliest)
    if len(set(bits)) == 1:                             # all-equal: no edge
        return (0, 0)
    rising = bits == sorted(bits)                       # 0..0 1..1
    falling = bits == sorted(bits, reverse=True)        # 1..1 0..0
    if not (rising or falling):
        return (0, 0)                                   # non-monotone glitch
    run = 1
    while run < 5 and bits[run] == bits[0]:
        run += 1
    return (run - 1, 1)                                 # run in 1..4 -> phase 0..3


@cocotb.test()
async def test_decode_truth_table(dut):
    for s in range(32):
        dut.samples.value = s
        await Timer(1, unit="ns")
        exp_phase, exp_valid = _expect(s)
        got_valid = int(dut.fine_valid.value)
        assert got_valid == exp_valid, f"s={s:05b}: fine_valid {got_valid} != {exp_valid}"
        if exp_valid:
            got_phase = int(dut.fine_phase.value)
            assert got_phase == exp_phase, f"s={s:05b}: fine_phase {got_phase} != {exp_phase}"
