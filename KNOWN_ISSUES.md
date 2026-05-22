# Known issues

Things the dashboard doesn't handle cleanly today, with enough context to
revisit when they actually bite.

## Third-party generation sources on the same AC bus

The inverter's `p_load_demand` register is computed by the inverter from
what it's delivering at its AC port plus/minus what flows through to the
grid meter. The inverter assumes it's the only generator on the AC bus.

If another inverter, a generator, or some other AC source is back-feeding
the same circuit:

- `p_load_demand` will under-report by however much the third party is
  contributing, and can go **negative** when the other source overproduces
  past the house's consumption.
- The Loads box would render a negative kW value (the value formatter is
  sign-aware but the sparkline and value column aren't designed to draw
  attention to it).
- The trunk animation still flows inverter → loads, even when the actual
  net flow has reversed.
- The EnergyBalance ledger's "Total Output" understates real output by the
  third-party contribution, which would show up as a persistent positive
  imbalance — easy to mistake for inverter conversion loss.

I haven't fixed this because every GivEnergy install I've seen is
single-inverter. If it ever becomes relevant we'd want either (a) a
separate "External" source node in the diagram, (b) a third-party-detected
annotation on the Grid or Loads box, or (c) a sign-aware Load renderer
that flips its visual when load is net negative.
