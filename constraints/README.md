# constraints/

Placeholder for the **hardware / synthesis** stage (not used by simulation).

When you move from simulation to a real FPGA, put your physical constraints
here — e.g. Xilinx `.xdc` files mapping top-level ports to package pins and
defining clock timing. Vivado (or your synthesis flow) reads these; the cocotb
simulation does not.

Suggested layout once you get there:

```
constraints/
  <board>.xdc        # pin assignments + I/O standards + clock constraints
synth/               # synthesis scripts / Vivado project (add when needed)
```

Until then this directory just reserves the spot so `rtl/` stays pure RTL.
