# ticket-reconciler

Reconciles Drupal registrations against Eventbrite sales, and runs the front desk.

One of five applications in the
[event-management stack](https://github.com/pu-shd/event-stack), built on
[eventkit](https://github.com/pu-shd/eventkit).

## What it does

- Matches registrations to Eventbrite payments and reports the difference.
- Front-desk check-in, one column per event day.
- Swag inventory, issuance and replacements.
- Waivers, refund overrides and manual payment links.
- Excel export of what is on screen, plus a full audit dump.

## Quickstart

```sh
docker-compose up            # http://localhost:8000
docker-compose run --rm test
```

## Routes

| Route | Auth | |
|---|---|---|
| `GET /` | user | dashboard |
| `GET /api/reports/registrations` | user | the reconciliation table |
| `POST /api/sync` | user | pull from Eventbrite |
| `POST /api/checkin` | user | `{person_key, day_key, state}` |
| `GET /api/changes?since=` | user | polling feed for the desk |
| `POST /api/drupal-webhook` | token | upsert |
| `GET /healthz` | none | liveness |

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

Add a Remote Post handler on your registration webform pointing at
`https://<app>.azurewebsites.net/api/drupal-webhook`, Completed and Updated,
method POST, type JSON. Custom options:

```yaml
headers:
  X-Drupal-Webhook-Token: <the token the toolkit printed>
```

**The nesting matters** — a flat key is ignored and every call 403s while the
registrant still sees success. Confirm with `GET /api/webhook/status`, which
reports counters and `unmapped_keys` and no attendee data.

Field keys are declared in
[drupal-event-forms](https://github.com/pu-shd/drupal-event-forms/blob/main/contracts/).

## Check-in day keys are ISO dates

`2027-06-28`, and a named event is `2027-06-30-banquet`. They come from
`schedule.checkin_days` in the event profile. Migration `0002` rewrites legacy
`"6/28"` keys by position and fails loudly on anything it does not recognise.

## Swag lives here, and only here

One application counts shirts. If you want a size on a badge, read it from the
profile in `nametag-press` rather than keeping a second inventory.

## Discount codes

Referenced by environment variable name, never by value. Set
`EVENTBRITE_DISCOUNT_*` as application settings.

## Deploying

```zsh
eventkit azure deploy --event my-event-2027 --dry-run   # prints every az command
eventkit azure deploy --event my-event-2027
```

Idempotent and resumable; it joins the event's existing resource group, plan and
registry or creates them. `deploy/app.conf` declares the settings and gates.
Every route is behind Easy Auth — it shows payment amounts.

CI/CD templates:

```zsh
TPL="$(python -c 'import eventkit.azure as a; print(a.templates_path())')"
cp "$TPL"/workflows/{deploy,test,backup}.yml .github/workflows/
```

Without Azure, the container runs anywhere:

```sh
docker build --target runtime -t ticket-reconciler .
docker run -p 8000:8000 \
  -v "$PWD/event-profile.yaml:/app/event-profile.yaml:ro" \
  -e DRUPAL_WEBHOOK_TOKEN="$(openssl rand -hex 32)" \
  -e AUTHORIZED_PRINCIPALS="you@example.edu" \
  -e DATABASE_URL="sqlite:////data/ticket-reconciler.db" \
  ticket-reconciler
```

→ [Deployment guide](https://github.com/pu-shd/eventkit/blob/main/docs/azure/README.md)

## Licence

MIT. Copyright (c) 2026 The Trustees of Princeton University.
