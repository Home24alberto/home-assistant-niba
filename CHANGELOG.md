# Changelog

## 2.0.4

- Prevent stale API totals and costs from carrying into a new billing period
  during a temporary endpoint outage.
- Validate numeric sensor states and guard nested user/hourly-curve data
  against unexpected response shapes.
- Harden contract, user, and hourly curve handling when optional fields have an
  unexpected shape.
- Extend tests for token renewal and invalid API responses.

## 2.0.3

- Keep Niba's period summary separate from curve-based calculations and expose
  dedicated API consumption sensors.
- Add safe diagnostics for daily curve freshness and temporary API errors.
- De-duplicate daily readings and ignore malformed or out-of-period records.
- Remove manual tariff setup. API consumption and energy cost estimates work
  with the account credentials alone.
- Keep the full invoice total unknown when the API does not provide it; do not
  substitute a tariff-based estimate that differs between customers.
- Add unit tests for API diagnostics and daily curve handling to CI.

## 2.0.2

- Separate Niba's period summary from curve-based calculations and expose
  dedicated API consumption sensors.
- Add safe diagnostics for daily curve freshness and temporary API errors.
- De-duplicate daily readings and ignore malformed or out-of-period records.
