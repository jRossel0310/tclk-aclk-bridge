# deploy/ - load a KR260 PL bitstream and run a reader

Generic flow for getting a Vivado design onto the KR260 and talking to its AXI
slave at `0x8000_0000` from Linux on the board. (See `tclk.md` for the live-TCLK
runbook and the "uart_echo example" section below for the original echo demo.)

## Artifacts in the flow

| Artifact | Made by | Role |
|----------|---------|------|
| `.bit` | Vivado (`hw.ps1 build`) | raw PL bitstream |
| `.bit.bin` | bootgen (auto, in `hw.ps1 build`) | what `fpgautil`/FPGA-manager loads |
| `.dtbo` | `dtc` from a `.dts` | device-tree overlay; needed for the UIO path |

## Build (PC)

```powershell
.\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk
```
Prints `BIT`, `BIN`, `MD5`, `SHA256` and writes `build-manifest.json`. Artifacts
land repo-local under `build\kria\<name>\<name>.runs\impl_1\`.

Optional copy to the board:
```powershell
.\hw.ps1 deploy -Name tclk -DeployHost ubuntu@kria
```

## Copying files to `aclk-timestamper` (Fermilab network, Kerberos)

The board on the lab network (`aclk-timestamper.fnal.gov`) authenticates via
GSSAPI/Kerberos, and Git Bash's `ssh`/`scp` cannot see the MIT Kerberos ticket,
so they fail to authenticate. Use PuTTY's `pscp` instead (typically
`C:\Program Files\PuTTY\pscp.exe`; add PuTTY to PATH to call it bare). It uses
the same GSSAPI path as the working PuTTY session, so with a valid ticket there
is no password prompt.

Prerequisites: on the lab network or VPN (to reach the board and the KDC), and
a live ticket (`klist` shows `krbtgt/FNAL.GOV`; renew with `kinit jrossel`).
An expired ticket is the usual cause of GSSAPI/permission failures.

```powershell
# laptop -> board
pscp -scp "C:\path\to\localfile.txt" ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/

# board -> laptop
pscp -scp ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/somefile.log "C:\Users\jacob\Downloads\"

# whole directory
pscp -scp -r "C:\path\to\folder" ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/
```

`-scp` forces the SCP protocol (pscp may default to SFTP; either works, `-scp`
keeps it predictable). If a saved PuTTY session named `aclk-timestamper` exists
(with the username and GSSAPI options configured), the session name can replace
the full `user@host`:

```powershell
pscp -scp "localfile.txt" aclk-timestamper:/home/ubuntu/
```

GUI alternative: WinSCP also supports GSSAPI/Kerberos (enable GSSAPI in its SSH
settings) and is what Fermilab's docs recommend for drag-and-drop transfers.

## Load on the board (UIO + overlay, preferred)

```bash
md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC MD5
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
ls -l /dev/uio*
```

- The `-o <overlay>.dtbo` form is required for the UIO readers: it creates
  `/dev/uioN` and releases PL reset.
- `-f Full` programs the PL but does NOT create a UIO device, so it is not
  equivalent; do not substitute it for the UIO flow.
- A cosmetic `OF: overlay: WARNING: memory leak will occur ...` on load is
  harmless.

## Readers

All readers import `readout_common.py` (shared register map + watchdog + drain loop);
`hw.ps1 deploy` copies it automatically. If you scp a reader by hand, copy
`readout_common.py` and `tclk_filter.py` alongside it.

Python readers mmap either `/dev/uioN` (offset 0) or `/dev/mem` (offset
`0x8000_0000`); register offsets are identical. Find the right UIO node via
`cat /sys/class/uio/uio*/name`. Run with `-u` for unbuffered output, e.g.:
```bash
sudo python3 -u tclk_read.py /dev/uio4 --drop 07,0F,BA,8F
```

### `/dev/mem` fallback

If UIO is unavailable or locked down, a root reader can mmap `/dev/mem` at the
AXI base directly (no overlay, no driver). Use this only if the UIO path is not
available; the overlay path is preferred because it also releases PL reset.

## Verifying the load matches your build

Compare the board-side `md5sum ~/<bit.bin>` against the `MD5` line printed by
`hw.ps1 build` (also recorded in `build-manifest.json`). Mismatch means a stale
copy on the board.

---

## Example: uart_echo (original demo)

The first design here, `uart_echo`, cross-wires an AXI UART Lite to the custom
`uart_echo` RTL inside the PL so a byte sent from the PS echoes back:

```
   PS  --AXI-->  AXI UART Lite  --tx-->  uart_echo.serial_in
                                <--rx--  uart_echo.serial_out
```

Build and run:
```powershell
.\hw.ps1 build            # design_name uart_echo
```
```bash
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
sudo python3 uart_echo_test.py
```
`uart_echo_test.py` historically used `/dev/mem` (`-f Full` load); it still works
that way, but the UIO + overlay flow above is the current default. Files
`uart_echo.bif` / `uart_echo.dts` are kept for this example and as overlay
sources; `template.bif` is the generic reference.
