# Security policy

## Reporting a vulnerability

Security reports for this tool and its protocol stack are gathered in one place
— the [givenergy-modbus security page](https://github.com/dewet22/givenergy-modbus/security):
use **Report a vulnerability** there rather than opening a public issue, or
email **givenergy-modbus@dewet.org** if you prefer. Reports that turn out to be
specific to this CLI will be handled here once triaged.

I aim to acknowledge reports within a week. This is a spare-time project with a
single maintainer, so a fix may take longer, but you'll hear back about what's
happening and when.

## Scope notes

- The CLI is read-only by design: it never issues Modbus write commands to the
  inverter. Anything that would change that posture is treated as
  security-relevant.
- The local Modbus-TCP interface on GivEnergy hardware has no authentication or
  encryption; that is a property of the device, not something this tool can fix.
  Network segmentation around the inverter is the user's control.
- Frame parsing of inverter traffic happens in the
  [givenergy-modbus](https://github.com/dewet22/givenergy-modbus) library —
  another reason reports are pooled there.

A point-in-time security audit of this codebase is published in
[SECURITY_AUDIT.md](SECURITY_AUDIT.md), with follow-up work tracked openly on the
issue tracker.
