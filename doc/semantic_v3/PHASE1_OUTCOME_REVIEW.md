# Phase 1 — outcome review (brand 20, all history, 2026-10-14)

Classifier: mage-ai `semantic-v3` `serve/semantic_v3/views/01_core_order_class.select.sql` (read-only SELECT, not applied). ₹ = gross ex-GST (`gross_revenue_excl_tax`).

```
    ┌─order_type───────────┬─outcome──────────────┬─outcome_evidence───────────────────────────────┬─────n─┬─gross_ex_gst─┬──cod─┐
 1. │ zero_value           │ active               │ active_delivered                               │     8 │          234 │    0 │
 2. │ zero_value           │ active               │ active_not_shipped                             │     3 │            2 │    0 │
 3. │ zero_value           │ active               │ active_shipped_no_delivery_status              │     3 │            1 │    0 │
 4. │ zero_value           │ exchanged            │ exchange_replacement_linked                    │     1 │          117 │    1 │
 5. │ zero_value           │ returned             │ return_delivered                               │     2 │          117 │    0 │
 6. │ zero_value           │ rto                  │ return_shipped_rto_evidence                    │     1 │            0 │    0 │
 7. │ test                 │ cancelled            │ cancel_not_shipped                             │     1 │            2 │    0 │
 8. │ sale                 │ active               │ active_delivered                               │ 17096 │     30785192 │ 7459 │
 9. │ sale                 │ active               │ active_shipped_no_delivery_status              │  1659 │      3193586 │  898 │
10. │ sale                 │ active               │ active_not_shipped                             │    96 │       198663 │   32 │
11. │ sale                 │ cancelled            │ cancel_not_shipped                             │   452 │      1144990 │  344 │
12. │ sale                 │ cancelled            │ cancel_cod_to_prepaid                          │    10 │        14465 │    5 │
13. │ sale                 │ exchange_in_progress │ exchange_marker_no_replacement                 │     2 │         4253 │    0 │
14. │ sale                 │ exchanged            │ exchange_replacement_linked                    │   366 │       801490 │   90 │
15. │ sale                 │ returned             │ return_delivered                               │   752 │      1716584 │  285 │
16. │ sale                 │ rto                  │ return_shipped_rto_evidence                    │   518 │       966129 │  430 │
17. │ sale                 │ rto                  │ cancel_shipped_rto_evidence                    │   256 │       236310 │  255 │
18. │ sale                 │ unknown              │ cancel_not_shipped_rto_tag                     │   949 │      1892736 │  922 │
19. │ sale                 │ unknown              │ return_request_no_delivery_no_money            │   707 │      1381489 │  667 │
20. │ sale                 │ unknown              │ cancel_shipped_no_rto_evidence                 │   346 │       701032 │  339 │
21. │ sale                 │ unknown              │ money_refund_no_return_evidence                │   131 │       293048 │   46 │
22. │ sale                 │ unknown              │ return_request_money_refund_no_delivery_status │   125 │       284235 │   15 │
23. │ sale                 │ unknown              │ cancel_after_delivery                          │    52 │        58157 │   49 │
24. │ sale                 │ unknown              │ active_with_rto_evidence                       │    46 │        88365 │   17 │
25. │ sale                 │ unknown              │ return_rto_evidence_but_delivered              │    11 │        24604 │    6 │
26. │ sale                 │ unknown              │ return_no_evidence                             │     5 │        13214 │    1 │
27. │ exchange_replacement │ active               │ active_delivered                               │   337 │       660922 │   15 │
28. │ exchange_replacement │ active               │ active_shipped_no_delivery_status              │    51 │       119478 │    0 │
29. │ exchange_replacement │ active               │ active_not_shipped                             │    13 │        36474 │    0 │
30. │ exchange_replacement │ cancelled            │ cancel_not_shipped                             │     8 │        30390 │    0 │
31. │ exchange_replacement │ cancelled            │ cancel_cod_to_prepaid                          │     1 │         2118 │    0 │
32. │ exchange_replacement │ exchange_in_progress │ exchange_marker_no_replacement                 │    79 │       175825 │    3 │
33. │ exchange_replacement │ returned             │ exchange_marker_money_refund                   │     7 │        11858 │    0 │
34. │ exchange_replacement │ unknown              │ cancel_shipped_no_rto_evidence                 │     2 │         2896 │    0 │
35. │ exchange_replacement │ unknown              │ active_with_rto_evidence                       │     1 │         2542 │    0 │
    └──────────────────────┴──────────────────────┴────────────────────────────────────────────────┴───────┴──────────────┴──────┘

```

## Samples per evidence code (5 random orders)

| evidence | orders |
|---|---|
| `active_with_rto_evidence` | ['TH/26-27/23230','TC-001/25-26/6548','TH/26-27/24257','TC-001/25-26/4511','TC-001/25-26/4425'] |
| `cancel_after_delivery` | ['TC-001/25-26/2674','TC-001/25-26/2359','TC-001/25-26/2316','TC-001/25-26/2888','TH/26-27/23438'] |
| `cancel_cod_to_prepaid` | ['TH/26-27/24078','TH/26-27/23341','TH/26-27/23812','TH/26-27/23721','TH/26-27/23826'] |
| `cancel_not_shipped` | ['TC-001/25-26/9070','TH/26-27/16796','TH/26-27/21544','TC-001/25-26/11129','TH/26-27/21697'] |
| `cancel_not_shipped_rto_tag` | ['TC-001/25-26/3167','TC-001/25-26/7305','TC-001/25-26/5143','TC-001/25-26/5074','TC-001/25-26/3906'] |
| `cancel_shipped_no_rto_evidence` | ['TC-001/25-26/9837','TC-001/25-26/9829','TC-001/25-26/9252','#1537','TC-001/25-26/8201'] |
| `cancel_shipped_rto_evidence` | ['TC-001/25-26/3962','TC-001/25-26/1654','TC-001/25-26/1701','TC-001/25-26/1609','TC-001/25-26/4025'] |
| `money_refund_no_return_evidence` | ['TC-001/25-26/8355','TC-001/25-26/8959','TC-001/25-26/9015','TC-001/25-26/9900','TC-001/25-26/9967'] |
| `return_no_evidence` | ['TC-001/25-26/3717','TC-001/25-26/4657','TC-001/25-26/4290','TH/26-27/20120','TC-001/25-26/4842'] |
| `return_request_money_refund_no_delivery_status` | ['TC-001/25-26/13176','TC-001/25-26/8083','TH/26-27/19894','TH/26-27/20993','TC-001/25-26/13325'] |
| `return_request_no_delivery_no_money` | ['TH/26-27/0001/14987','TC-001/25-26/11865','TH/26-27/15600','TH/26-27/0001/14212','TC-001/25-26/13469'] |
| `return_rto_evidence_but_delivered` | ['TC-001/25-26/2836','TC-001/25-26/1918','TH/26-27/23930','TH/26-27/18814','TH/26-27/24636'] |
| `return_shipped_rto_evidence` | ['TH/26-27/20141','TH/26-27/24832','TH/26-27/21456','TC-001/25-26/6737','TH/26-27/21743'] |
| `` |  |
