# Alerting on failed requests at the ingress

Written 2026-10-22, after diagnosing a broken beets-flask `/inbox` page. Figures
come from 30 days of ingress-nginx access logs in VictoriaLogs (2026-09-22 to
2026-10-21), and were taken before Prometheus had any ingress metrics.

## Where this stands

Prometheus scrapes both ingress-nginx controllers through the chart's
ServiceMonitor, enabled 2026-10-22. Before then the HelmReleases set
`metrics.enabled` without `serviceMonitor.enabled`, so no ingress metric had
ever been collected. The `prometheus.io/scrape` pod annotations they carried
were read by nothing and are gone.

No alert has been built yet. Asked whether to add a 5xx alert straight away,
the user chose: **"ServiceMonitors only for now"** — collect about a week of
ingress metrics in Prometheus first, then design alerts against them rather
than against log-derived estimates. Earliest useful start: 2026-10-29.

## What the log figures show

- **501 is crawler noise and must be excluded.** 11,511 in 30 days, almost all
  bots POSTing to `apps-demo-8000` on the external controller.
- **podbook-rebound returns a steady trickle of 500s**: 2–58 a day, every day,
  414 in total. A raw-count threshold would never clear; use a share of the
  host's traffic, or fix the app first.
- **Real outages appear as 502/503 bursts.** trello-randomizer: 96 errors on
  2026-09-23 and 188 in one ten-minute window on 2026-09-27. plex, linkding
  and headscale together on 2026-09-22 around 22:40. headscale on its own
  around restarts (2026-09-30, 2026-10-06, 2026-10-09). Before paging on these,
  check whether pod-level alerts already fired for those windows.
- **Stalled backends do not show up as 5xx.** When the upstream times out at
  60s, nginx retries the request (`proxy_next_upstream`). Each backend has a
  single pod, so the retry goes to the same pod, and the final status (usually
  200) is what the access log and `nginx_ingress_controller_requests` record.
  57 requests hid a 504 this way in 30 days (beets 12, headscale 45; headscale's
  are likely long-polls). Verified on beets: its 2026-10-22 00:55 stall shows
  as `status="200"`, with a 64.7s request time. A stall is only visible as
  request duration, and the default duration buckets top out at 10s, so
  telling 15s from 65s needs `--time-buckets` on the controller. Websockets,
  headscale long-polls and Plex streams are legitimately long and would need
  excluding.
- **Requests nginx rejects as malformed are invisible to both log and
  metric.** An oversized HTTP/2 request line is reset before the request is
  logged; nginx notes it only at `info` level, and the controller runs at
  `notice`. That is how the beets `/inbox` failure went unseen. Raising the log
  level was considered and not done: it logs routine connection events for
  every host.

## Decisions binding this work

- ingress-nginx is retired upstream and scheduled for replacement
  ([ingress-nginx-migration-prompt.md](ingress-nginx-migration-prompt.md)).
  Whatever is built on `nginx_ingress_*` will be rewritten at that migration,
  so keep it small. If the migration is close, build the alerting on the
  replacement instead.
- Follow `docs/README.md`: rules that resolve on their own, verified both
  firing and silent.

## The prompt

> Read `todos/ingress-error-alerting.md`. Prometheus has been scraping
> ingress-nginx since 2026-10-22. Using that data, propose a per-host 5xx alert
> (501 excluded, threshold as a share of traffic) and say whether a
> stalled-backend alert on request duration is worth its exclusions. For each,
> show which windows in the collected data it would have fired on, and whether
> pod-level alerts already covered them. Check how close the ingress-nginx
> migration is before building on `nginx_ingress_*`. Do not deploy rules until
> I have seen the backtest.
