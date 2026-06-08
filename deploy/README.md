# deploy/ — test uart_echo on the KR260 over the network

This design lets you exercise the custom `uart_echo` RTL from Linux on the board,
with **no external adapter and no PMOD wiring**. The bitstream contains an AMD
**AXI UART Lite** (AXI base `0x8000_0000`) whose serial `tx`/`rx` are cross-wired
to `uart_echo` *inside the PL*:

```
   PS  --AXI-->  AXI UART Lite  --tx-->  uart_echo.serial_in
                                <--rx--  uart_echo.serial_out
```

Send a byte from the UART Lite → it passes through your receiver → FIFO →
transmitter → comes back in the UART Lite RX FIFO. Read it back over SSH and you've
proven the custom UART works on real hardware.

## Files

| File | Purpose |
|------|---------|
| `uart_echo.bif` | bootgen recipe: Vivado `.bit` → loadable `.bit.bin` |
| `uart_echo_test.py` | mmaps the UART Lite via `/dev/mem` and checks the echo |
| `uart_echo.dts` | (fallback) device-tree overlay if you use the UIO path |

## Steps

### 1. Build the bitstream (PC)
```powershell
.\hw.ps1 build
```
→ `…\kria-builds\uart_echo\uart_echo.runs\impl_1\uart_echo_bd_wrapper.bit`

### 2. Convert .bit → .bit.bin (PC, in a Vivado/Vitis shell)
Copy `uart_echo.bif` next to the `.bit`, then:
```bat
bootgen -arch zynqmp -process_bitstream bin -image uart_echo.bif
```
→ `uart_echo_bd_wrapper.bit.bin`

### 3. Copy to the board
```bash
scp uart_echo_bd_wrapper.bit.bin uart_echo_test.py ubuntu@<board-ip>:~
```

### 4. Load the bitstream and run the test (on the board, over SSH)
```bash
sudo xmutil unloadapp                                   # free the PL
sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -f Full   # program the PL
sudo python3 uart_echo_test.py
```
Expected:
```
sent 0x41  got 0x41  OK
sent 0x55  got 0x55  OK
...
PASS — uart_echo works on hardware
```

## Why `/dev/mem`?

The UART Lite sits at a fixed AXI address (`0x8000_0000`) reachable from the PS
once the bitstream is loaded, so a root process can mmap it directly — no kernel
driver, no device-tree node, no interrupt. Simplest possible path.

## Fallback: UIO (if `/dev/mem` is locked down)

Some hardened kernels block `/dev/mem` access to device memory. If
`uart_echo_test.py` fails at `os.open("/dev/mem")`, switch to UIO:

1. Compile the overlay: `dtc -@ -O dtb -o uart_echo.dtbo uart_echo.dts`
2. Load it alongside the bitstream (xmutil app flow, or `fpgautil -b ... -o uart_echo.dtbo`).
3. Find the device: `ls -l /dev/uio*` (read `/sys/class/uio/uio*/name`).
4. In `uart_echo_test.py`, change the mmap source from `/dev/mem` (offset `BASE`)
   to the matching `/dev/uioN` (offset `0`). The register offsets are identical.

> The `uart_echo.dts` labels (`&fpga_full`, `&amba`) and the address must match
> your board image — verify against `/proc/device-tree` if the overlay won't load.
