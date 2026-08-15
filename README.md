# ticket-reconciler

Reconciles Drupal registrations against Eventbrite ticket sales, and runs the
front desk: check-in, swag, fee waivers.

One of five applications in the [event-management stack](https://github.com/pu-shd/eventkit).

## What it does

- **Reconciliation.** Joins registrations to Eventbrite orders and derives a status
  per person: `Pending`, `Paid`, `Complete`, `Exempt`, `Waived`, `Refunded`,
  `Cancelled`, or `Unmatched`.
- **Front desk.** Per-day check-in with a four-state cycle, swag issuance and size
  swaps, fee waivers with a recorded justification, and a refund override for
  someone who refunded but turned up anyway.
- **Manual linking** for the common case where somebody bought a ticket with a
  different address than they registered with.
- **Saved groups**, so the desk can filter to a delegation quickly.
- **CSV export** and a full JSON backup.

## The reconciliation engine is a pure function

`build_report(registrants, payments, profile)` takes lists and returns rows. No
session, no settings, no clock. In the predecessor this was 130 lines inside a route
handler, which made the most valuable logic in the stack also the least testable.

`tests/test_reconcile.py` pins the truth table case by case, including the two
guards that matter:

- A payment claimed by somebody's **manual link** is not also matched to a different
  person who happens to share the address.
- One payment is never matched to **two** people. The webform makes email unique so
  it should not arise, but the cost of getting it wrong is a doubled revenue figure
  and two people shown as Paid for one ticket.

## Check-in day keys are ISO dates

Configured in the event profile, validated on every write. The predecessor used bare
`"6/28"` strings: year-less, colliding across events, and ambiguous to parse — both
`"7/1"` and `"07/01"` appear in its live data. Posting an unconfigured key here is a
400 listing the valid ones.

## Discount codes are never in the repository

The profile carries the **name** of an environment variable
(`discount_code_env: EVENTBRITE_DISCOUNT_GENERAL`), never a code — the profile is
committed and is served to the browser. Codes live only in App Service settings. The
predecessor had two live codes, an institutional email-domain branch, and the event
slug as string literals in a route handler.

## The clear endpoint

`POST /api/admin/clear` needs an allow-listed principal **and**
`ENABLE_DESTRUCTIVE_OPS=True`, and writes a `DESTRUCTIVE:` audit line naming who ran
it. There is no CI path to it.

The predecessor's equivalent had **no authentication dependency at all**: the only
gate was a confirmation phrase published in its own repository, so any anonymous
caller who knew the hostname could delete every registration and payment. It was open
because a workflow curled it with no header. That workflow is gone; enable the flag,
act, disable it.

## Quickstart

```sh
docker-compose run --rm test     # the whole suite, same command as CI
docker-compose up app            # http://localhost:8000
```

## Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `DRUPAL_WEBHOOK_TOKEN` | **required** | No default; placeholders rejected. `openssl rand -hex 32`. |
| `AUTHORIZED_PRINCIPALS` | `""` | **Empty means deny all.** |
| `DATABASE_URL` | `sqlite:///./data/ticket-reconciler.db` | `sqlite:////home/…` on Azure; Postgres via the `postgres` extra. |
| `ENABLE_RESTORE` | `False` | Restore truncates tables. |
| `ENABLE_DESTRUCTIVE_OPS` | `False` | Gates the clear endpoint. |
| `EVENTBRITE_API_TOKEN` / `EVENTBRITE_EVENT_ID` | unset | Sync returns 503 until both are set. |
| `ENABLE_AUTO_SYNC` / `AUTO_SYNC_INTERVAL_MINUTES` | `False` / `60` | Clamped to 3–1440. |
| `NOTIFY_TRANSPORT` | `log` | `smtp`, `resend` or `acs` for real mail. A missing credential can never block a deploy. |
| `EVENT_PROFILE` | unset | Path to `event-profile.yaml`. |

Admin routes depend on Azure Easy Auth being configured in front of the app;
provisioning does not do it. Verify with `az webapp auth show`.

## Drupal wiring

Remote Post handler → `POST /api/drupal-webhook`, token under a **`headers:`** key in
Custom options. Completed and Updated URLs; Draft and Deleted empty.

Consumed: `email`, a name, plus `uuid`, `sid`, `serial`, `tickets_sold_separately`,
`destination_url`, `t_shirt_size`, `attendee_status`.

An exemption checkbox hidden by `#states` is **absent** from the payload, not false —
absent reads as exempt, which is what the form intends for speakers and organizers.

## Swag lives here, and only here

`nametag-press` deliberately has no swag fields. Two applications counting shirts
independently is how you oversell mediums.

## Licence

MIT. Copyright (c) 2026 The Trustees of Princeton University.
