# SEM-18: End-to-End Certification Report
*Generated 2026-10-16T09:40:44.950798 UTC*

## Summary
- Total questions: 40
- PASS: 40
- PARTIAL: 0
- FAIL/ERROR: 0
- Pass rate: 100.0%

## Results

### AD01 [ad_delivery]
**Question:** What were Meta spend, impressions, link clicks and landing-page views from 1–11 October?
**Status:** PASS
**Elapsed (ms):** 237.9
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'ad_spend': '497666.96', 'impressions': '1604779', 'link_clicks': '21015', 'landing_page_views': '0'}

### AD02 [ad_delivery]
**Question:** How did Meta delivery change each day?
**Status:** PASS
**Elapsed (ms):** 319.4
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'report_date.day': '2026-10-01T00:00:00.000', 'report_date': '2026-10-01T00:00:00.000', 'ad_spend': '36104.61', 'impressions': '110871', 'link_clicks': '1577', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-02T00:00:00.000', 'report_date': '2026-10-02T00:00:00.000', 'ad_spend': '24878.71', 'impressions': '68588', 'link_clicks': '1068', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-03T00:00:00.000', 'report_date': '2026-10-03T00:00:00.000', 'ad_spend': '44393.450000000004', 'impressions': '110565', 'link_clicks': '1748', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-04T00:00:00.000', 'report_date': '2026-10-04T00:00:00.000', 'ad_spend': '41909.26999999998', 'impressions': '297188', 'link_clicks': '1970', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-05T00:00:00.000', 'report_date': '2026-10-05T00:00:00.000', 'ad_spend': '62397.95000000001', 'impressions': '259116', 'link_clicks': '2150', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-06T00:00:00.000', 'report_date': '2026-10-06T00:00:00.000', 'ad_spend': '59856.38999999999', 'impressions': '212002', 'link_clicks': '2557', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-07T00:00:00.000', 'report_date': '2026-10-07T00:00:00.000', 'ad_spend': '60538.029999999984', 'impressions': '146032', 'link_clicks': '2447', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-08T00:00:00.000', 'report_date': '2026-10-08T00:00:00.000', 'ad_spend': '58860.200000000004', 'impressions': '123190', 'link_clicks': '2397', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-09T00:00:00.000', 'report_date': '2026-10-09T00:00:00.000', 'ad_spend': '47301.46', 'impressions': '131377', 'link_clicks': '2901', 'landing_page_views': '0'}
  {'report_date.day': '2026-10-10T00:00:00.000', 'report_date': '2026-10-10T00:00:00.000', 'ad_spend': '33067.53', 'impressions': '81780', 'link_clicks': '1168', 'landing_page_views': '0'}

### AD03 [ad_delivery]
**Question:** Which 10 Meta campaigns spent the most, and what delivery did they generate?
**Status:** PASS
**Elapsed (ms):** 282.9
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': '120250695088250783', 'campaign_name': 'TH-383-SUSPENDER-26SEP-ADV+', 'ad_spend': '55793.41', 'impressions': '178071', 'link_clicks': '2906', 'landing_page_views': '0'}
  {'campaign_id': '120250726752410783', 'campaign_name': 'TH-383-SUSPENDER-29SEP', 'ad_spend': '49163.21', 'impressions': '250693', 'link_clicks': '3807', 'landing_page_views': '0'}
  {'campaign_id': '120250597947340783', 'campaign_name': 'TH-222-WINGBIRD-23SEP', 'ad_spend': '49099.409999999996', 'impressions': '98630', 'link_clicks': '1952', 'landing_page_views': '0'}
  {'campaign_id': '120250690866980783', 'campaign_name': 'TH-336-PAWTECH-26SEP', 'ad_spend': '46780.030000000006', 'impressions': '124652', 'link_clicks': '1602', 'landing_page_views': '0'}
  {'campaign_id': '120250681379400783', 'campaign_name': 'TH-149-SCRATCHLOUNGE-25SEP', 'ad_spend': '42872.08000000001', 'impressions': '53835', 'link_clicks': '1342', 'landing_page_views': '0'}
  {'campaign_id': '120250695726350783', 'campaign_name': 'CAT TOY CS-26SEP', 'ad_spend': '26968.949999999997', 'impressions': '51943', 'link_clicks': '979', 'landing_page_views': '0'}
  {'campaign_id': '120250684363180783', 'campaign_name': 'TH-445-PROSUSPENDERBOOTS-25SEP', 'ad_spend': '21743.12', 'impressions': '91256', 'link_clicks': '1085', 'landing_page_views': '0'}
  {'campaign_id': '120250823301270783', 'campaign_name': 'TH-149-SCRATCHLOUNGE-6OCT', 'ad_spend': '20481.17', 'impressions': '49800', 'link_clicks': '1043', 'landing_page_views': '0'}
  {'campaign_id': '120250667421200783', 'campaign_name': 'DOG TOY CS-24SEP', 'ad_spend': '18283.43', 'impressions': '34619', 'link_clicks': '576', 'landing_page_views': '0'}
  {'campaign_id': '120250648999490783', 'campaign_name': 'TH-012-SNUGBOO-23SEP', 'ad_spend': '16573', 'impressions': '29189', 'link_clicks': '570', 'landing_page_views': '0'}

### AD04 [ad_delivery]
**Question:** Which 10 Meta ads generated the most landing-page views?
**Status:** PASS
**Elapsed (ms):** 305.9
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'ad_id': '120250684363190783', 'ad_name': 'TH-445-PROSUSPENDER0BOOTS-UGC', 'ad_spend': '402.68', 'impressions': '643', 'link_clicks': '8', 'landing_page_views': '0'}
  {'ad_id': '120250868269590783', 'ad_name': 'TH-447-CABANADREAMBED_CF347_ET199', 'ad_spend': '1554.4', 'impressions': '2743', 'link_clicks': '36', 'landing_page_views': '0'}
  {'ad_id': '120250893878710783', 'ad_name': 'TH-229-BLUEJAY SNEAKS_CF301_IM258', 'ad_spend': '1068.49', 'impressions': '1745', 'link_clicks': '20', 'landing_page_views': '0'}
  {'ad_id': '120250695786200783', 'ad_name': 'CAT TOYS-CS', 'ad_spend': '13651.93', 'impressions': '29804', 'link_clicks': '527', 'landing_page_views': '0'}
  {'ad_id': '120250754903360783', 'ad_name': 'TH-445-PROSUSPENDERBOOTS_ET129_CF137-1st-oct', 'ad_spend': '21340.439999999995', 'impressions': '90613', 'link_clicks': '1077', 'landing_page_views': '0'}
  {'ad_id': '120250835984080783', 'ad_name': 'TH-279-BATHEASEBRUSH-07OCT', 'ad_spend': '5947.139999999999', 'impressions': '4525', 'link_clicks': '65', 'landing_page_views': '0'}
  {'ad_id': '120250650599760783', 'ad_name': 'TH-452-FLEXIDINEFEEDER-CS', 'ad_spend': '0', 'impressions': '0', 'link_clicks': '0', 'landing_page_views': '0'}
  {'ad_id': '120250712044050783', 'ad_name': 'TH-448-HANDSFREEDOGBELT_IM258_SI207', 'ad_spend': '0', 'impressions': '0', 'link_clicks': '0', 'landing_page_views': '0'}
  {'ad_id': '120250667421220783', 'ad_name': 'DOG TOYS-CS', 'ad_spend': '18283.43', 'impressions': '34619', 'link_clicks': '576', 'landing_page_views': '0'}
  {'ad_id': '120250726752430783', 'ad_name': 'TH-383-SUSPENDER-UGC', 'ad_spend': '49163.21', 'impressions': '250693', 'link_clicks': '3807', 'landing_page_views': '0'}
**Warnings:**
  - ad_id: Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign (device and network are available) and show no ad group or ad (L-03).
  - ad_name: Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign (device and network are available) and show no ad group or ad (L-03).

### AD05 [ad_delivery]
**Question:** How did Meta delivery vary across ad sets?
**Status:** PASS
**Elapsed (ms):** 358.1
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': '120250695088250783', 'adset_id': '120250695088240783', 'adset_name': 'Broad Advantage+', 'ad_spend': '55793.41', 'impressions': '178071', 'link_clicks': '2906', 'landing_page_views': '0'}
  {'campaign_id': '120250726752410783', 'adset_id': '120250726752450783', 'adset_name': 'Broad Advantage+', 'ad_spend': '49163.21', 'impressions': '250693', 'link_clicks': '3807', 'landing_page_views': '0'}
  {'campaign_id': '120250597947340783', 'adset_id': '120250598012070783', 'adset_name': 'Broad Advantage+', 'ad_spend': '49099.409999999996', 'impressions': '98630', 'link_clicks': '1952', 'landing_page_views': '0'}
  {'campaign_id': '120250690866980783', 'adset_id': '120250690867020783', 'adset_name': 'Broad Advantage+', 'ad_spend': '46780.030000000006', 'impressions': '124652', 'link_clicks': '1602', 'landing_page_views': '0'}
  {'campaign_id': '120250681379400783', 'adset_id': '120250681379390783', 'adset_name': 'Broad Advantage+', 'ad_spend': '42872.08000000001', 'impressions': '53835', 'link_clicks': '1342', 'landing_page_views': '0'}
  {'campaign_id': '120250695726350783', 'adset_id': '120250695726370783', 'adset_name': 'Broad Advantage+', 'ad_spend': '26968.949999999997', 'impressions': '51943', 'link_clicks': '979', 'landing_page_views': '0'}
  {'campaign_id': '120250684363180783', 'adset_id': '120250684363170783', 'adset_name': 'Broad Advantage+', 'ad_spend': '21743.12', 'impressions': '91256', 'link_clicks': '1085', 'landing_page_views': '0'}
  {'campaign_id': '120250823301270783', 'adset_id': '120250823301290783', 'adset_name': 'Broad Advantage+', 'ad_spend': '20481.17', 'impressions': '49800', 'link_clicks': '1043', 'landing_page_views': '0'}
  {'campaign_id': '120250667421200783', 'adset_id': '120250667421210783', 'adset_name': 'Broad Advantage+', 'ad_spend': '18283.43', 'impressions': '34619', 'link_clicks': '576', 'landing_page_views': '0'}
  {'campaign_id': '120250648999490783', 'adset_id': '120250649150440783', 'adset_name': 'Broad Advantage+', 'ad_spend': '16573', 'impressions': '29189', 'link_clicks': '570', 'landing_page_views': '0'}
**Warnings:**
  - adset_id: Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign (device and network are available) and show no ad group or ad (L-03).
  - adset_name: Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign (device and network are available) and show no ad group or ad (L-03).

### AD06 [ad_delivery]
**Question:** How did delivery vary by creative type?
**Status:** PASS
**Elapsed (ms):** 238.2
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'creative_type': 'VIDEO', 'ad_spend': '355319.5699999999', 'impressions': '1312463', 'link_clicks': '15489', 'landing_page_views': '0'}
  {'creative_type': 'SHARE', 'ad_spend': '142287.11999999997', 'impressions': '292213', 'link_clicks': '5526', 'landing_page_views': '0'}
  {'creative_type': '', 'ad_spend': '60.27', 'impressions': '103', 'link_clicks': '0', 'landing_page_views': '0'}
**Warnings:**
  - creative_type: Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign (device and network are available) and show no ad group or ad (L-03).

### AD07 [ad_delivery]
**Question:** How did delivery vary by campaign objective?
**Status:** PASS
**Elapsed (ms):** 307.3
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_objective': 'OUTCOME_SALES', 'ad_spend': '496707.0400000001', 'impressions': '1244697', 'link_clicks': '20465', 'landing_page_views': '0'}
  {'campaign_objective': 'OUTCOME_AWARENESS', 'ad_spend': '720.14', 'impressions': '353886', 'link_clicks': '0', 'landing_page_views': '0'}
  {'campaign_objective': 'OUTCOME_TRAFFIC', 'ad_spend': '239.78', 'impressions': '6196', 'link_clicks': '550', 'landing_page_views': '0'}

### AD08 [ad_delivery]
**Question:** How did daily spend and click volume change for the two Suspender campaigns?
**Status:** PASS
**Elapsed (ms):** 471.3
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': '120250726752410783', 'campaign_name': 'TH-383-SUSPENDER-29SEP', 'report_date.day': '2026-10-08T00:00:00.000', 'report_date': '2026-10-08T00:00:00.000', 'ad_spend': '5304.14', 'impressions': '19033', 'link_clicks': '289', 'landing_page_views': '0'}
  {'campaign_id': '120250695088250783', 'campaign_name': 'TH-383-SUSPENDER-26SEP-ADV+', 'report_date.day': '2026-10-08T00:00:00.000', 'report_date': '2026-10-08T00:00:00.000', 'ad_spend': '5939.43', 'impressions': '16831', 'link_clicks': '322', 'landing_page_views': '0'}
  {'campaign_id': '120250726752410783', 'campaign_name': 'TH-383-SUSPENDER-29SEP', 'report_date.day': '2026-10-09T00:00:00.000', 'report_date': '2026-10-09T00:00:00.000', 'ad_spend': '10357.33', 'impressions': '49037', 'link_clicks': '969', 'landing_page_views': '0'}
  {'campaign_id': '120250695088250783', 'campaign_name': 'TH-383-SUSPENDER-26SEP-ADV+', 'report_date.day': '2026-10-09T00:00:00.000', 'report_date': '2026-10-09T00:00:00.000', 'ad_spend': '7375.82', 'impressions': '20319', 'link_clicks': '354', 'landing_page_views': '0'}
  {'campaign_id': '120250695088250783', 'campaign_name': 'TH-383-SUSPENDER-26SEP-ADV+', 'report_date.day': '2026-10-10T00:00:00.000', 'report_date': '2026-10-10T00:00:00.000', 'ad_spend': '2062.44', 'impressions': '5537', 'link_clicks': '73', 'landing_page_views': '0'}
  {'campaign_id': '120250726752410783', 'campaign_name': 'TH-383-SUSPENDER-29SEP', 'report_date.day': '2026-10-10T00:00:00.000', 'report_date': '2026-10-10T00:00:00.000', 'ad_spend': '1510.35', 'impressions': '6094', 'link_clicks': '63', 'landing_page_views': '0'}

### AD09 [ad_delivery]
**Question:** How did weekly spend, impressions and link clicks vary by paid platform in September?
**Status:** PASS
**Elapsed (ms):** 382.7
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'ad_platform': 'meta', 'report_date.week': '2026-08-31T00:00:00.000', 'report_date': '2026-08-31T00:00:00.000', 'ad_spend': '301802.99000000005', 'impressions': '492216', 'link_clicks': '7575', 'landing_page_views': '8660'}
  {'ad_platform': 'google', 'report_date.week': '2026-08-31T00:00:00.000', 'report_date': '2026-08-31T00:00:00.000', 'ad_spend': '128789.85336100006', 'impressions': '630122', 'link_clicks': None, 'landing_page_views': '7838'}
  {'ad_platform': 'google', 'report_date.week': '2026-09-07T00:00:00.000', 'report_date': '2026-09-07T00:00:00.000', 'ad_spend': '146967.851413', 'impressions': '787444', 'link_clicks': None, 'landing_page_views': '8755'}
  {'ad_platform': 'meta', 'report_date.week': '2026-09-07T00:00:00.000', 'report_date': '2026-09-07T00:00:00.000', 'ad_spend': '312915.57999999996', 'impressions': '600197', 'link_clicks': '8175', 'landing_page_views': '9510'}
  {'ad_platform': 'meta', 'report_date.week': '2026-09-14T00:00:00.000', 'report_date': '2026-09-14T00:00:00.000', 'ad_spend': '1625.84', 'impressions': '4102', 'link_clicks': '56', 'landing_page_views': '1071'}
  {'ad_platform': 'google', 'report_date.week': '2026-09-14T00:00:00.000', 'report_date': '2026-09-14T00:00:00.000', 'ad_spend': '7648.4518259999995', 'impressions': '33525', 'link_clicks': None, 'landing_page_views': '1015'}
  {'ad_platform': 'google', 'report_date.week': '2026-09-21T00:00:00.000', 'report_date': '2026-09-21T00:00:00.000', 'ad_spend': '27798.049503000002', 'impressions': '76576', 'link_clicks': None, 'landing_page_views': '1312'}
  {'ad_platform': 'meta', 'report_date.week': '2026-09-21T00:00:00.000', 'report_date': '2026-09-21T00:00:00.000', 'ad_spend': '142247.15999999992', 'impressions': '263580', 'link_clicks': '3958', 'landing_page_views': '6678'}
  {'ad_platform': 'google', 'report_date.week': '2026-09-28T00:00:00.000', 'report_date': '2026-09-28T00:00:00.000', 'ad_spend': '27074.781364000002', 'impressions': '110764', 'link_clicks': None, 'landing_page_views': '1483'}
  {'ad_platform': 'meta', 'report_date.week': '2026-09-28T00:00:00.000', 'report_date': '2026-09-28T00:00:00.000', 'ad_spend': '96451.50000000001', 'impressions': '225595', 'link_clicks': '3418', 'landing_page_views': '4058'}

### AD10 [ad_delivery]
**Question:** What were September spend, impressions and clicks by platform and account?
**Status:** PASS
**Elapsed (ms):** 299.9
**Expected view:** paid_media
**Time basis:** report_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'ad_platform': 'meta', 'account_id': '1685189008684458', 'ad_spend': '855043.0699999994', 'impressions': '1585690', 'link_clicks': '23182', 'landing_page_views': '0'}
  {'ad_platform': 'google', 'account_id': '7713902754', 'ad_spend': '338278.98746700014', 'impressions': '1638431', 'link_clicks': None, 'landing_page_views': '0'}
  {'ad_platform': 'meta', 'account_id': None, 'ad_spend': '0', 'impressions': '0', 'link_clicks': None, 'landing_page_views': '29977'}
  {'ad_platform': 'google', 'account_id': None, 'ad_spend': '0', 'impressions': '0', 'link_clicks': None, 'landing_page_views': '20403'}

### PN01 [pnl]
**Question:** What were overall reported net sales, costs, contribution, ad spend and net profit?
**Status:** PASS
**Elapsed (ms):** 208.3
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'order_pnl_net_sales': '825557.6459013197', 'net_cogs': '344504.8291000001', 'contribution_margin': '481052.8168013199', 'order_pnl_ad_spend': '619778.825811', 'net_profit': '-138726.0090096802'}

### PN02 [pnl]
**Question:** How did reported P&L change each day?
**Status:** PASS
**Elapsed (ms):** 221.8
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'order_date.day': '2026-10-01T00:00:00.000', 'order_date': '2026-10-01T00:00:00.000', 'order_pnl_net_sales': '72113.43556659212', 'net_cogs': '28373.958800000008', 'contribution_margin': '43739.47676659212', 'order_pnl_ad_spend': '45967.58830999999', 'net_profit': '-2228.111543407877'}
  {'order_date.day': '2026-10-02T00:00:00.000', 'order_date': '2026-10-02T00:00:00.000', 'order_pnl_net_sales': '39403.26370928209', 'net_cogs': '17352.6951', 'contribution_margin': '22050.5686092821', 'order_pnl_ad_spend': '35504.786819999994', 'net_profit': '-13454.218210717892'}
  {'order_date.day': '2026-10-03T00:00:00.000', 'order_date': '2026-10-03T00:00:00.000', 'order_pnl_net_sales': '104404.14281599756', 'net_cogs': '46727.36260000001', 'contribution_margin': '57676.780215997525', 'order_pnl_ad_spend': '54602.83326300002', 'net_profit': '3073.9469529975136'}
  {'order_date.day': '2026-10-04T00:00:00.000', 'order_date': '2026-10-04T00:00:00.000', 'order_pnl_net_sales': '80315.91809007905', 'net_cogs': '32368.6445', 'contribution_margin': '47947.273590079065', 'order_pnl_ad_spend': '52968.01943499998', 'net_profit': '-5020.745844920925'}
  {'order_date.day': '2026-10-05T00:00:00.000', 'order_date': '2026-10-05T00:00:00.000', 'order_pnl_net_sales': '110905.60126581539', 'net_cogs': '45323.57470000001', 'contribution_margin': '65582.02656581535', 'order_pnl_ad_spend': '73811.90662899999', 'net_profit': '-8229.880063184639'}
  {'order_date.day': '2026-10-06T00:00:00.000', 'order_date': '2026-10-06T00:00:00.000', 'order_pnl_net_sales': '95471.70818576032', 'net_cogs': '39198.50379999999', 'contribution_margin': '56273.204385760335', 'order_pnl_ad_spend': '70633.76350200002', 'net_profit': '-14360.559116239674'}
  {'order_date.day': '2026-10-07T00:00:00.000', 'order_date': '2026-10-07T00:00:00.000', 'order_pnl_net_sales': '86621.4983830322', 'net_cogs': '37735.29149999999', 'contribution_margin': '48886.20688303222', 'order_pnl_ad_spend': '71171.657463', 'net_profit': '-22285.45057996781'}
  {'order_date.day': '2026-10-08T00:00:00.000', 'order_date': '2026-10-08T00:00:00.000', 'order_pnl_net_sales': '100399.77273701385', 'net_cogs': '42673.8194', 'contribution_margin': '57725.95333701385', 'order_pnl_ad_spend': '69851.37416800001', 'net_profit': '-12125.420830986166'}
  {'order_date.day': '2026-10-09T00:00:00.000', 'order_date': '2026-10-09T00:00:00.000', 'order_pnl_net_sales': '53879.72564368779', 'net_cogs': '20134.279000000002', 'contribution_margin': '33745.4466436878', 'order_pnl_ad_spend': '60101.68897599998', 'net_profit': '-26356.24233231218'}
  {'order_date.day': '2026-10-10T00:00:00.000', 'order_date': '2026-10-10T00:00:00.000', 'order_pnl_net_sales': '47233.32455679933', 'net_cogs': '19795.179800000005', 'contribution_margin': '27438.14475679934', 'order_pnl_ad_spend': '45085.33984', 'net_profit': '-17647.195083200648'}

### PN03 [pnl]
**Question:** Which finance channels contributed the most reported net profit?
**Status:** PASS
**Elapsed (ms):** 480.2
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'finance_channel': 'unattributed', 'order_pnl_net_sales': '65333.07503917804', 'net_cogs': '24442.870100000004', 'contribution_margin': '40890.20493917804', 'order_pnl_ad_spend': '0', 'net_profit': '40890.20493917804'}
  {'finance_channel': 'whatsapp', 'order_pnl_net_sales': '8764.403003337042', 'net_cogs': '3231.4899', 'contribution_margin': '5532.913103337041', 'order_pnl_ad_spend': '0', 'net_profit': '5532.913103337041'}
  {'finance_channel': 'organic', 'order_pnl_net_sales': '4447.55', 'net_cogs': '2021.6546999999996', 'contribution_margin': '2425.8953', 'order_pnl_ad_spend': '0', 'net_profit': '2425.8953'}
  {'finance_channel': 'google', 'order_pnl_net_sales': '194454.25128079928', 'net_cogs': '90168.00899999998', 'contribution_margin': '104286.2422807992', 'order_pnl_ad_spend': '122111.86581100003', 'net_profit': '-17825.623530200763'}
  {'finance_channel': 'meta', 'order_pnl_net_sales': '552558.3665780054', 'net_cogs': '224640.80539999995', 'contribution_margin': '327917.56117800565', 'order_pnl_ad_spend': '497666.9600000007', 'net_profit': '-169749.39882199443'}

### PN04 [pnl]
**Question:** Which Meta campaign buckets had the highest reported net profit?
**Status:** PASS
**Elapsed (ms):** 356.3
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': None, 'campaign_name': None, 'order_pnl_net_sales': '56849.97350114387', 'net_cogs': '22730.095500000007', 'contribution_margin': '34119.87800114388', 'order_pnl_ad_spend': '0', 'net_profit': '34119.87800114388', 'channel_orders': '33'}
  {'campaign_id': '120250437240110783', 'campaign_name': 'TH-222-WINGBIRD-08SEP', 'order_pnl_net_sales': '2667.9700000000003', 'net_cogs': '1092.6344', 'contribution_margin': '1575.3356000000003', 'order_pnl_ad_spend': '0', 'net_profit': '1575.3356000000003', 'channel_orders': '1'}
  {'campaign_id': '120250884319280783', 'campaign_name': 'TH-287-MISTCALMBRUSH-10OCT-UGC', 'order_pnl_net_sales': '3811.27', 'net_cogs': '1257.9385', 'contribution_margin': '2553.3315000000002', 'order_pnl_ad_spend': '1099.29', 'net_profit': '1454.0415000000003', 'channel_orders': '1'}
  {'campaign_id': '120250332036640783', 'campaign_name': 'TH-385-DUCKTOY-1SEP', 'order_pnl_net_sales': '2244.0713885554223', 'net_cogs': '958.8815999999999', 'contribution_margin': '1285.1897885554224', 'order_pnl_ad_spend': '0', 'net_profit': '1285.1897885554224', 'channel_orders': '1'}
  {'campaign_id': '120247805774670783', 'campaign_name': 'TH-383-SUSPENDER-17JUNE', 'order_pnl_net_sales': '1906.02', 'net_cogs': '621.4973', 'contribution_margin': '1284.5227', 'order_pnl_ad_spend': '0', 'net_profit': '1284.5227', 'channel_orders': '1'}
  {'campaign_id': '120249388862310783', 'campaign_name': 'TH-383-SUSPENDER-17JULY-ADV+', 'order_pnl_net_sales': '2117.8', 'net_cogs': '934.7357999999999', 'contribution_margin': '1183.0642000000003', 'order_pnl_ad_spend': '0', 'net_profit': '1183.0642000000003', 'channel_orders': '1'}
  {'campaign_id': '120249948202410783', 'campaign_name': 'TH-054-TOTE-12AUG', 'order_pnl_net_sales': '1600.17', 'net_cogs': '574.6785', 'contribution_margin': '1025.4915', 'order_pnl_ad_spend': '0', 'net_profit': '1025.4915', 'channel_orders': '1'}
  {'campaign_id': '120250331650630783', 'campaign_name': 'TH-348-FUSIONHARNESS-1SEP', 'order_pnl_net_sales': '2159.3254901960786', 'net_cogs': '1137.5900000000001', 'contribution_margin': '1021.7354901960786', 'order_pnl_ad_spend': '0', 'net_profit': '1021.7354901960786', 'channel_orders': '1'}
  {'campaign_id': '120250652935110783', 'campaign_name': 'TH-068_WILD-23SEP-CS', 'order_pnl_net_sales': '1694.07', 'net_cogs': '779.2375', 'contribution_margin': '914.8325000000001', 'order_pnl_ad_spend': '0', 'net_profit': '914.8325000000001', 'channel_orders': '1'}
  {'campaign_id': '120250695459110783', 'campaign_name': 'TH-452-FLEXIDINEFEEDER-26SEP-CS', 'order_pnl_net_sales': '1100.85', 'net_cogs': '429.2558', 'contribution_margin': '671.5942', 'order_pnl_ad_spend': '0', 'net_profit': '671.5942', 'channel_orders': '1'}

### PN05 [pnl]
**Question:** Which Meta campaign buckets had the largest reported losses?
**Status:** PASS
**Elapsed (ms):** 328.1
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': '120250695088250783', 'campaign_name': 'TH-383-SUSPENDER-26SEP-ADV+', 'order_pnl_net_sales': '40188.525899056316', 'net_cogs': '12847.5451', 'contribution_margin': '27340.98079905632', 'order_pnl_ad_spend': '55793.409999999996', 'net_profit': '-28452.429200943683', 'channel_orders': '20'}
  {'campaign_id': '120250726752410783', 'campaign_name': 'TH-383-SUSPENDER-29SEP', 'order_pnl_net_sales': '40075.80759314253', 'net_cogs': '13504.594099999998', 'contribution_margin': '26571.213493142524', 'order_pnl_ad_spend': '49163.20999999999', 'net_profit': '-22591.996506857482', 'channel_orders': '22'}
  {'campaign_id': '120250597947340783', 'campaign_name': 'TH-222-WINGBIRD-23SEP', 'order_pnl_net_sales': '58956.326248015226', 'net_cogs': '23813.4191', 'contribution_margin': '35142.907148015234', 'order_pnl_ad_spend': '49099.41', 'net_profit': '-13956.50285198477', 'channel_orders': '52'}
  {'campaign_id': '120250690866980783', 'campaign_name': 'TH-336-PAWTECH-26SEP', 'order_pnl_net_sales': '57980.65632644405', 'net_cogs': '22665.141299999996', 'contribution_margin': '35315.51502644407', 'order_pnl_ad_spend': '46780.03000000001', 'net_profit': '-11464.514973555935', 'channel_orders': '29'}
  {'campaign_id': '120250695726350783', 'campaign_name': 'CAT TOY CS-26SEP', 'order_pnl_net_sales': '25190.345044617585', 'net_cogs': '9614.297800000002', 'contribution_margin': '15576.047244617585', 'order_pnl_ad_spend': '26968.95', 'net_profit': '-11392.90275538241', 'channel_orders': '16'}
  {'campaign_id': '120250667421200783', 'campaign_name': 'DOG TOY CS-24SEP', 'order_pnl_net_sales': '12693.208553505483', 'net_cogs': '5742.0329', 'contribution_margin': '6951.175653505482', 'order_pnl_ad_spend': '18283.430000000004', 'net_profit': '-11332.254346494514', 'channel_orders': '9'}
  {'campaign_id': '120250788048700783', 'campaign_name': 'TH-348-FUSIONHARNESS-03OCT', 'order_pnl_net_sales': '10924.593757503004', 'net_cogs': '6291.566000000001', 'contribution_margin': '4633.027757503002', 'order_pnl_ad_spend': '14667.409999999996', 'net_profit': '-10034.382242496995', 'channel_orders': '5'}
  {'campaign_id': '120250681379400783', 'campaign_name': 'TH-149-SCRATCHLOUNGE-25SEP', 'order_pnl_net_sales': '59179.55849539817', 'net_cogs': '24683.477', 'contribution_margin': '34496.08149539817', 'order_pnl_ad_spend': '42872.08', 'net_profit': '-8375.998504601834', 'channel_orders': '26'}
  {'campaign_id': '120250737739100783', 'campaign_name': 'DOG-SHOES-CTL-30SEP', 'order_pnl_net_sales': '2100.0854374048', 'net_cogs': '715.2253000000001', 'contribution_margin': '1384.8601374047998', 'order_pnl_ad_spend': '8952.41', 'net_profit': '-7567.5498625952005', 'channel_orders': '1'}
  {'campaign_id': '120250684363180783', 'campaign_name': 'TH-445-PROSUSPENDERBOOTS-25SEP', 'order_pnl_net_sales': '26298.50192755955', 'net_cogs': '11372.839099999997', 'contribution_margin': '14925.662827559552', 'order_pnl_ad_spend': '21743.120000000003', 'net_profit': '-6817.457172440452', 'channel_orders': '11'}

### PN06 [pnl]
**Question:** Which Meta ad buckets had the highest reported net profit?
**Status:** PASS
**Elapsed (ms):** 541.3
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': None, 'ad_id': None, 'ad_name': None, 'order_pnl_net_sales': '56849.97350114387', 'net_cogs': '22730.095500000007', 'contribution_margin': '34119.87800114388', 'order_pnl_ad_spend': '0', 'net_profit': '34119.87800114388', 'channel_orders': '33'}
  {'campaign_id': '120250437240110783', 'ad_id': '120250437240120783', 'ad_name': 'TH-222-WINGBIRD', 'order_pnl_net_sales': '2667.9700000000003', 'net_cogs': '1092.6344', 'contribution_margin': '1575.3356000000003', 'order_pnl_ad_spend': '0', 'net_profit': '1575.3356000000003', 'channel_orders': '1'}
  {'campaign_id': '120250884319280783', 'ad_id': '120250884319290783', 'ad_name': 'TH-287-MISTCALMBRUSH-UGC', 'order_pnl_net_sales': '3811.27', 'net_cogs': '1257.9385', 'contribution_margin': '2553.3315000000002', 'order_pnl_ad_spend': '1099.29', 'net_profit': '1454.0415000000003', 'channel_orders': '1'}
  {'campaign_id': '120250332036640783', 'ad_id': '120250332036660783', 'ad_name': 'TH-385-DUCKTOY', 'order_pnl_net_sales': '2244.0713885554223', 'net_cogs': '958.8815999999999', 'contribution_margin': '1285.1897885554224', 'order_pnl_ad_spend': '0', 'net_profit': '1285.1897885554224', 'channel_orders': '1'}
  {'campaign_id': '120247805774670783', 'ad_id': '120247805774680783', 'ad_name': 'TH-383-SUSPENDER-4XL- MAROON_ET141_IM256', 'order_pnl_net_sales': '1906.02', 'net_cogs': '621.4973', 'contribution_margin': '1284.5227', 'order_pnl_ad_spend': '0', 'net_profit': '1284.5227', 'channel_orders': '1'}
  {'campaign_id': '120249388862310783', 'ad_id': '120249388862290783', 'ad_name': 'TH-383-SUSPENDER_VP032_IM258', 'order_pnl_net_sales': '2117.8', 'net_cogs': '934.7357999999999', 'contribution_margin': '1183.0642000000003', 'order_pnl_ad_spend': '0', 'net_profit': '1183.0642000000003', 'channel_orders': '1'}
  {'campaign_id': '120249948202410783', 'ad_id': '120249948202400783', 'ad_name': 'TH-054-TOTE-CS-1', 'order_pnl_net_sales': '1600.17', 'net_cogs': '574.6785', 'contribution_margin': '1025.4915', 'order_pnl_ad_spend': '0', 'net_profit': '1025.4915', 'channel_orders': '1'}
  {'campaign_id': '120250331650630783', 'ad_id': '120250389172070783', 'ad_name': 'TH-348-FUSIONHARNESS_ET164_VP034-4SEP', 'order_pnl_net_sales': '2159.3254901960786', 'net_cogs': '1137.5900000000001', 'contribution_margin': '1021.7354901960786', 'order_pnl_ad_spend': '0', 'net_profit': '1021.7354901960786', 'channel_orders': '1'}
  {'campaign_id': '120250652935110783', 'ad_id': '120250652935120783', 'ad_name': 'TH-068_WILD-CS', 'order_pnl_net_sales': '1694.07', 'net_cogs': '779.2375', 'contribution_margin': '914.8325000000001', 'order_pnl_ad_spend': '0', 'net_profit': '914.8325000000001', 'channel_orders': '1'}
  {'campaign_id': '120250695459110783', 'ad_id': '120250695548450783', 'ad_name': 'TH-452-FLEXIDINEFEEDER-CS', 'order_pnl_net_sales': '1100.85', 'net_cogs': '429.2558', 'contribution_margin': '671.5942', 'order_pnl_ad_spend': '0', 'net_profit': '671.5942', 'channel_orders': '1'}
**Warnings:**
  - ad_id: Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign (device and network are available) and show no ad group or ad (L-03).
  - ad_name: Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign (device and network are available) and show no ad group or ad (L-03).

### PN07 [pnl]
**Question:** How large were product, shipping, packaging, payment gateway and RTO costs?
**Status:** PASS
**Elapsed (ms):** 209.4
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'product_cost': '267716.82', 'shipping_cost': '33962.170000000006', 'packaging_cost': '7700', 'payment_gateway_fees': '5488.5948', 'rto_cost': '1855.51', 'net_cogs': '344504.8291000001'}

### PN08 [pnl]
**Question:** What gross sales, discounts, returns and cancellations form the P&L sales bridge?
**Status:** PASS
**Elapsed (ms):** 273.3
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'order_pnl_gross_sales': '870709.06', 'order_pnl_discounts': '32494.171', 'order_pnl_return_revenue': '13069.09', 'order_pnl_cancel_revenue': '10290.77', 'order_pnl_net_sales': '825557.6459013197'}

### PN09 [pnl]
**Question:** How did reported P&L differ by payment bucket?
**Status:** PASS
**Elapsed (ms):** 234.4
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'payment_bucket': 'manual', 'order_pnl_net_sales': '793181.9459013211', 'net_cogs': '329143.43589999975', 'contribution_margin': '464038.51000132016', 'order_pnl_ad_spend': '0', 'net_profit': '464038.51000132016'}
  {'payment_bucket': 'online', 'order_pnl_net_sales': '21244.840000000004', 'net_cogs': '9394.1021', 'contribution_margin': '11850.7379', 'order_pnl_ad_spend': '0', 'net_profit': '11850.7379'}
  {'payment_bucket': 'cod', 'order_pnl_net_sales': '11130.86', 'net_cogs': '5967.2910999999995', 'contribution_margin': '5163.5689', 'order_pnl_ad_spend': '0', 'net_profit': '5163.5689'}
  {'payment_bucket': None, 'order_pnl_net_sales': '0', 'net_cogs': '0', 'contribution_margin': '0', 'order_pnl_ad_spend': '619778.8258110011', 'net_profit': '-619778.8258110011'}

### PN10 [pnl]
**Question:** How much P&L was represented by each row type and finance channel?
**Status:** PASS
**Elapsed (ms):** 675.0
**Expected view:** order_pnl
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'order_pnl_row_type': 'orders', 'finance_channel': 'meta', 'order_pnl_net_sales': '552558.3665780054', 'net_cogs': '224640.80539999995', 'contribution_margin': '327917.56117800565', 'order_pnl_ad_spend': '0', 'net_profit': '327917.56117800565'}
  {'order_pnl_row_type': 'orders', 'finance_channel': 'google', 'order_pnl_net_sales': '194454.25128079928', 'net_cogs': '90168.00899999998', 'contribution_margin': '104286.2422807992', 'order_pnl_ad_spend': '0', 'net_profit': '104286.2422807992'}
  {'order_pnl_row_type': 'orders', 'finance_channel': 'unattributed', 'order_pnl_net_sales': '65333.07503917804', 'net_cogs': '24442.870100000004', 'contribution_margin': '40890.20493917804', 'order_pnl_ad_spend': '0', 'net_profit': '40890.20493917804'}
  {'order_pnl_row_type': 'orders', 'finance_channel': 'whatsapp', 'order_pnl_net_sales': '8764.403003337042', 'net_cogs': '3231.4899', 'contribution_margin': '5532.913103337041', 'order_pnl_ad_spend': '0', 'net_profit': '5532.913103337041'}
  {'order_pnl_row_type': 'orders', 'finance_channel': 'organic', 'order_pnl_net_sales': '4447.55', 'net_cogs': '2021.6546999999996', 'contribution_margin': '2425.8953', 'order_pnl_ad_spend': '0', 'net_profit': '2425.8953'}
  {'order_pnl_row_type': 'ad_spend', 'finance_channel': 'google', 'order_pnl_net_sales': '0', 'net_cogs': '0', 'contribution_margin': '0', 'order_pnl_ad_spend': '122111.86581100003', 'net_profit': '-122111.86581100003'}
  {'order_pnl_row_type': 'ad_spend', 'finance_channel': 'meta', 'order_pnl_net_sales': '0', 'net_cogs': '0', 'contribution_margin': '0', 'order_pnl_ad_spend': '497666.9600000007', 'net_profit': '-497666.9600000007'}

### WB01 [web_analytics]
**Question:** What were all-channel session volume and session conversion indicators?
**Status:** PASS
**Elapsed (ms):** 199.8
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'sessions': '47091', 'product_view_rate': '0.672081714127965', 'add_to_cart_rate': '0.030239323862309146', 'checkout_rate': '0.013293410630481409', 'conversion_rate': '0.009322375825529295'}

### WB02 [web_analytics]
**Question:** How did session volume and conversion indicators change each day?
**Status:** PASS
**Elapsed (ms):** 951.5
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'session_date.day': '2026-10-01T00:00:00.000', 'session_date': '2026-10-01T00:00:00.000', 'sessions': '4419', 'product_view_rate': '0.6250282869427473', 'add_to_cart_rate': '0.02964471599909482', 'checkout_rate': '0.014482914686580674', 'conversion_rate': '0.008599230595157275'}
  {'session_date.day': '2026-10-02T00:00:00.000', 'session_date': '2026-10-02T00:00:00.000', 'sessions': '2933', 'product_view_rate': '0.6829185134674395', 'add_to_cart_rate': '0.021479713603818614', 'checkout_rate': '0.010228435049437436', 'conversion_rate': '0.0064780088646437094'}
  {'session_date.day': '2026-10-03T00:00:00.000', 'session_date': '2026-10-03T00:00:00.000', 'sessions': '3932', 'product_view_rate': '0.6757375381485249', 'add_to_cart_rate': '0.033316378433367246', 'checkout_rate': '0.0211088504577823', 'conversion_rate': '0.013733468972533061'}
  {'session_date.day': '2026-10-04T00:00:00.000', 'session_date': '2026-10-04T00:00:00.000', 'sessions': '3981', 'product_view_rate': '0.6892740517457925', 'add_to_cart_rate': '0.03491585028887214', 'checkout_rate': '0.013062044712383824', 'conversion_rate': '0.01105249937201708'}
  {'session_date.day': '2026-10-05T00:00:00.000', 'session_date': '2026-10-05T00:00:00.000', 'sessions': '4047', 'product_view_rate': '0.6881640721522115', 'add_to_cart_rate': '0.03607610575735112', 'checkout_rate': '0.017296763034346428', 'conversion_rate': '0.012601927353595256'}
  {'session_date.day': '2026-10-06T00:00:00.000', 'session_date': '2026-10-06T00:00:00.000', 'sessions': '5110', 'product_view_rate': '0.6690802348336595', 'add_to_cart_rate': '0.027592954990215263', 'checkout_rate': '0.015655577299412915', 'conversion_rate': '0.010958904109589041'}
  {'session_date.day': '2026-10-07T00:00:00.000', 'session_date': '2026-10-07T00:00:00.000', 'sessions': '5185', 'product_view_rate': '0.7324975891996143', 'add_to_cart_rate': '0.03413693346190935', 'checkout_rate': '0.01407907425265188', 'conversion_rate': '0.009643201542912247'}
  {'session_date.day': '2026-10-08T00:00:00.000', 'session_date': '2026-10-08T00:00:00.000', 'sessions': '5041', 'product_view_rate': '0.6687165244991073', 'add_to_cart_rate': '0.0361039476294386', 'checkout_rate': '0.013489387026383654', 'conversion_rate': '0.011108906962904185'}
  {'session_date.day': '2026-10-09T00:00:00.000', 'session_date': '2026-10-09T00:00:00.000', 'sessions': '5091', 'product_view_rate': '0.681987821646042', 'add_to_cart_rate': '0.02258888234138676', 'checkout_rate': '0.007267727362011393', 'conversion_rate': '0.004910626595953644'}
  {'session_date.day': '2026-10-10T00:00:00.000', 'session_date': '2026-10-10T00:00:00.000', 'sessions': '3929', 'product_view_rate': '0.6411300585390685', 'add_to_cart_rate': '0.02621532196487656', 'checkout_rate': '0.0111987783150929', 'conversion_rate': '0.006617459913463986'}

### WB03 [web_analytics]
**Question:** Which traffic channels had the highest session volume and how did they convert?
**Status:** PASS
**Elapsed (ms):** 448.4
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'channel': 'ig_feed', 'sessions': '16932', 'product_view_rate': '0.8079966926529648', 'add_to_cart_rate': '0.04116465863453815', 'checkout_rate': '0.015473659343255374', 'conversion_rate': '0.014056224899598393'}
  {'channel': 'unattributed', 'sessions': '12664', 'product_view_rate': '0.5695672773215413', 'add_to_cart_rate': '0.007580543272267846', 'checkout_rate': '0.009238787113076437', 'conversion_rate': '0.0021320277953253315'}
  {'channel': 'fb_feed', 'sessions': '6812', 'product_view_rate': '0.780093951849677', 'add_to_cart_rate': '0.035672342924251324', 'checkout_rate': '0.011450381679389313', 'conversion_rate': '0.009835584263065179'}
  {'channel': 'google_search', 'sessions': '5409', 'product_view_rate': '0.22203734516546497', 'add_to_cart_rate': '0.02329450915141431', 'checkout_rate': '0.01238676280273618', 'conversion_rate': '0.0066555740432612314'}
  {'channel': 'google_pmax', 'sessions': '2896', 'product_view_rate': '0.8774171270718232', 'add_to_cart_rate': '0.06526243093922653', 'checkout_rate': '0.024171270718232045', 'conversion_rate': '0.020027624309392266'}
  {'channel': 'google_shopping', 'sessions': '1161', 'product_view_rate': '0.7097329888027563', 'add_to_cart_rate': '0.028423772609819122', 'checkout_rate': '0.01636520241171404', 'conversion_rate': '0.004306632213608958'}
  {'channel': 'google_organic', 'sessions': '824', 'product_view_rate': '0.7439320388349514', 'add_to_cart_rate': '0.0024271844660194173', 'checkout_rate': '0.0024271844660194173', 'conversion_rate': '0'}
  {'channel': 'whatsapp', 'sessions': '198', 'product_view_rate': '0.5707070707070707', 'add_to_cart_rate': '0.12626262626262627', 'checkout_rate': '0.025252525252525252', 'conversion_rate': '0.020202020202020204'}
  {'channel': 'meta_organic', 'sessions': '61', 'product_view_rate': '0.5737704918032787', 'add_to_cart_rate': '0.03278688524590164', 'checkout_rate': '0.01639344262295082', 'conversion_rate': '0.01639344262295082'}
  {'channel': 'ai_chatgpt', 'sessions': '38', 'product_view_rate': '0.868421052631579', 'add_to_cart_rate': '0.07894736842105263', 'checkout_rate': '0.02631578947368421', 'conversion_rate': '0.02631578947368421'}

### WB04 [web_analytics]
**Question:** How did Meta sessions and conversion indicators vary by campaign?
**Status:** PASS
**Elapsed (ms):** 741.2
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': '120250726752410783', 'campaign_name': 'TH-383-SUSPENDER-29SEP', 'sessions': '4179', 'product_view_rate': '0.8186168939937785', 'add_to_cart_rate': '0.017468293850203397', 'checkout_rate': '0.005264417324718832', 'conversion_rate': '0.005025125628140704'}
  {'campaign_id': None, 'campaign_name': None, 'sessions': '2680', 'product_view_rate': '0.8735074626865672', 'add_to_cart_rate': '0.03805970149253731', 'checkout_rate': '0.012686567164179104', 'conversion_rate': '0.012686567164179104'}
  {'campaign_id': '120250695088250783', 'campaign_name': 'TH-383-SUSPENDER-26SEP-ADV+', 'sessions': '2396', 'product_view_rate': '0.8301335559265443', 'add_to_cart_rate': '0.03547579298831385', 'checkout_rate': '0.009181969949916527', 'conversion_rate': '0.007512520868113523'}
  {'campaign_id': '120250597947340783', 'campaign_name': 'TH-222-WINGBIRD-23SEP', 'sessions': '1930', 'product_view_rate': '0.8658031088082901', 'add_to_cart_rate': '0.07253886010362694', 'checkout_rate': '0.027461139896373058', 'conversion_rate': '0.026424870466321245'}
  {'campaign_id': '120250690866980783', 'campaign_name': 'TH-336-PAWTECH-26SEP', 'sessions': '1607', 'product_view_rate': '0.8537647790914747', 'add_to_cart_rate': '0.04044803982576229', 'checkout_rate': '0.019912881144990666', 'conversion_rate': '0.01804604853764779'}
  {'campaign_id': '120250681379400783', 'campaign_name': 'TH-149-SCRATCHLOUNGE-25SEP', 'sessions': '1426', 'product_view_rate': '0.8380084151472651', 'add_to_cart_rate': '0.05750350631136045', 'checkout_rate': '0.019635343618513323', 'conversion_rate': '0.018934081346423562'}
  {'campaign_id': '120250684363180783', 'campaign_name': 'TH-445-PROSUSPENDERBOOTS-25SEP', 'sessions': '1225', 'product_view_rate': '0.8138775510204082', 'add_to_cart_rate': '0.027755102040816326', 'checkout_rate': '0.010612244897959184', 'conversion_rate': '0.00816326530612245'}
  {'campaign_id': '120250823301270783', 'campaign_name': 'TH-149-SCRATCHLOUNGE-6OCT', 'sessions': '1075', 'product_view_rate': '0.8213953488372093', 'add_to_cart_rate': '0.05116279069767442', 'checkout_rate': '0.013023255813953489', 'conversion_rate': '0.012093023255813953'}
  {'campaign_id': '120250695726350783', 'campaign_name': 'CAT TOY CS-26SEP', 'sessions': '1000', 'product_view_rate': '0.326', 'add_to_cart_rate': '0.04', 'checkout_rate': '0.018', 'conversion_rate': '0.014'}
  {'campaign_id': '120250822013190783', 'campaign_name': 'DOG DAD T-SHIRT-CS-06OCT', 'sessions': '747', 'product_view_rate': '0.8380187416331994', 'add_to_cart_rate': '0.030789825970548863', 'checkout_rate': '0.014725568942436412', 'conversion_rate': '0.012048192771084338'}

### WB05 [web_analytics]
**Question:** How did website session volume, bounce and conversion vary by device?
**Status:** PASS
**Elapsed (ms):** 223.5
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'device_type': None, 'sessions': '47091', 'session_bounce_rate': '0.327918285872035', 'conversion_rate': '0.009322375825529295'}

### WB06 [web_analytics]
**Question:** Which landing pages attracted the most sessions, and how did those sessions convert?
**Status:** PASS
**Elapsed (ms):** 280.3
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'landing_page_path': '/', 'sessions': '9762', 'product_view_rate': '0.2623437820118828', 'add_to_cart_rate': '0.016902274124154886', 'checkout_rate': '0.011473058799426348', 'conversion_rate': '0.0045072730997746365'}
  {'landing_page_path': '/products/pawveralls-suspender-boots', 'sessions': '7888', 'product_view_rate': '0.8326572008113591', 'add_to_cart_rate': '0.02865111561866126', 'checkout_rate': '0.009254563894523327', 'conversion_rate': '0.0069726166328600405'}
  {'landing_page_path': '/products/meow-scratch-lounge-cat-scratcher-relax-bed', 'sessions': '2802', 'product_view_rate': '0.8219129193433262', 'add_to_cart_rate': '0.05710206995003569', 'checkout_rate': '0.019628836545324768', 'conversion_rate': '0.019628836545324768'}
  {'landing_page_path': '/products/shop-wingbird-smart-pet-toys-by-tilting-heads', 'sessions': '2224', 'product_view_rate': '0.8435251798561151', 'add_to_cart_rate': '0.06879496402877698', 'checkout_rate': '0.026528776978417268', 'conversion_rate': '0.02473021582733813'}
  {'landing_page_path': '/products/pawveralls-pro-suspender-boots-for-dogs-waterproof-paw-protection-tilting-heads', 'sessions': '1873', 'product_view_rate': '0.8350240256273358', 'add_to_cart_rate': '0.034703683929524824', 'checkout_rate': '0.014415376401494928', 'conversion_rate': '0.011745862253069941'}
  {'landing_page_path': '/products/shop-electric-paw-cleaner-with-paw-cleanser-by-tilting-heads', 'sessions': '1848', 'product_view_rate': '0.8668831168831169', 'add_to_cart_rate': '0.040584415584415584', 'checkout_rate': '0.01893939393939394', 'conversion_rate': '0.016774891774891776'}
  {'landing_page_path': '/products/pocketpup-tote-bag', 'sessions': '1108', 'product_view_rate': '0.8673285198555957', 'add_to_cart_rate': '0.0388086642599278', 'checkout_rate': '0.01444043321299639', 'conversion_rate': '0.010830324909747292'}
  {'landing_page_path': '/collections/play-instinct-cats', 'sessions': '896', 'product_view_rate': '0.23660714285714285', 'add_to_cart_rate': '0.03125', 'checkout_rate': '0.008928571428571428', 'conversion_rate': '0.008928571428571428'}
  {'landing_page_path': '/collections/play-instinct-dogs', 'sessions': '862', 'product_view_rate': '0.20417633410672853', 'add_to_cart_rate': '0.04640371229698376', 'checkout_rate': '0.016241299303944315', 'conversion_rate': '0.016241299303944315'}
  {'landing_page_path': '/products/dog-dad-club-t-shirt-everyday-statement-wear-for-dog-lovers', 'sessions': '801', 'product_view_rate': '0.8514357053682896', 'add_to_cart_rate': '0.02871410736579276', 'checkout_rate': '0.009987515605493134', 'conversion_rate': '0.009987515605493134'}

### WB07 [web_analytics]
**Question:** What were the supported ATC-to-checkout and checkout-to-purchase session ratios?
**Status:** PASS
**Elapsed (ms):** 178.7
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'sessions': '47091', 'add_to_cart_to_checkout_rate': '0.4396067415730337', 'checkout_to_purchase_rate': '0.7012779552715654'}

### WB08 [web_analytics]
**Question:** How did bounce and purchase conversion vary by browser and operating system?
**Status:** PASS
**Elapsed (ms):** 237.1
**Expected view:** web_sessions
**Time basis:** session_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'browser_family': None, 'os_family': None, 'sessions': '47091', 'session_bounce_rate': '0.327918285872035', 'conversion_rate': '0.009322375825529295'}

### WB09 [web_analytics]
**Question:** Which product pages generated the most product-view and add-to-cart events?
**Status:** PASS
**Elapsed (ms):** 433.9
**Expected view:** web_event_detail
**Time basis:** event_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'page_path': '/products/pawveralls-suspender-boots', 'sku': 'TH-383-SUSPENDER-XS-MAROON', 'product_title': 'Pawveralls Suspender Boots', 'event_product_views': '9943', 'event_add_to_carts': '117'}
  {'page_path': '/products/pawveralls-pro-suspender-boots-for-dogs-waterproof-paw-protection-tilting-heads', 'sku': 'TH-445-PROSUSPENDERBOOTS-XS', 'product_title': 'Pawveralls Pro Suspender Boots', 'event_product_views': '3413', 'event_add_to_carts': '32'}
  {'page_path': '/products/meow-scratch-lounge-cat-scratcher-relax-bed', 'sku': 'TH-149-SCRATCHLOUNGE', 'product_title': 'Meow Scratch Lounge', 'event_product_views': '3166', 'event_add_to_carts': '295'}
  {'page_path': '/products/shop-wingbird-smart-pet-toys-by-tilting-heads', 'sku': 'TH-222-WINGBIRD', 'product_title': 'WingBird Smart Flapping Toy', 'event_product_views': '2714', 'event_add_to_carts': '379'}
  {'page_path': '/products/shop-electric-paw-cleaner-with-paw-cleanser-by-tilting-heads', 'sku': 'TH-336-PAWTECH-BLUE', 'product_title': 'PawTech Cleaner with Paw Cleanser 500ml', 'event_product_views': '2141', 'event_add_to_carts': '175'}
  {'page_path': '/products/pocketpup-tote-bag', 'sku': 'TH-054-TOTE-YELLOW', 'product_title': 'PocketPup Tote Bag (For Small Dogs & Cats) - Up to 6.5kg', 'event_product_views': '1356', 'event_add_to_carts': '49'}
  {'page_path': '/products/snugsole-pet-boots', 'sku': 'TH-012-SNUGBOO-XS', 'product_title': 'SnugSole Boots', 'event_product_views': '1256', 'event_add_to_carts': '33'}
  {'page_path': '/products/dog-dad-club-t-shirt-everyday-statement-wear-for-dog-lovers', 'sku': 'TH-359-DADCLUBTSHIRT-XS', 'product_title': 'Dog Dad Club T-Shirt', 'event_product_views': '994', 'event_add_to_carts': '1'}
  {'page_path': '/products/wildtrail-boots-winter-ready-waterproof-non-slip-built-for-all-terrains', 'sku': 'TH-068-WILD-S', 'product_title': 'WildTrail Boots', 'event_product_views': '952', 'event_add_to_carts': '16'}
  {'page_path': '/products/fusiongrip-harness-2-in-1-dog-harness-with-retractable-leash', 'sku': 'TH-348-FUSIONHARNESS-XS', 'product_title': 'FusionGrip Harness', 'event_product_views': '875', 'event_add_to_carts': '20'}

### WB10 [web_analytics]
**Question:** How did daily page-view and site-search event volumes vary by traffic channel?
**Status:** PASS
**Elapsed (ms):** 948.7
**Expected view:** web_event_detail
**Time basis:** event_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'channel': 'audience_network', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '27', 'event_site_searches': '0'}
  {'channel': 'organic_search', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '17', 'event_site_searches': '0'}
  {'channel': 'whatsapp', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '89', 'event_site_searches': '0'}
  {'channel': '', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '0', 'event_site_searches': '0'}
  {'channel': 'google_shopping', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '373', 'event_site_searches': '0'}
  {'channel': 'google_pmax', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '1835', 'event_site_searches': '9'}
  {'channel': 'ig_feed', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '5359', 'event_site_searches': '5'}
  {'channel': 'google_search', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '2038', 'event_site_searches': '17'}
  {'channel': 'fb_feed', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '1781', 'event_site_searches': '0'}
  {'channel': 'unattributed', 'event_date.day': '2026-10-01T00:00:00.000', 'event_date': '2026-10-01T00:00:00.000', 'event_page_views': '3600', 'event_site_searches': '17'}

### CM01 [commerce]
**Question:** How many orders were placed and what were gross sales, discounts, paid sales and commerce net sales?
**Status:** PASS
**Elapsed (ms):** 1438.0
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'orders': '453', 'gross_sales': '870709.0599999999', 'discounts': '32494.171000000002', 'paid_sales': '1047498.8999999997', 'net_sales': '825557.6459013197'}

### CM02 [commerce]
**Question:** How did daily orders and sales change?
**Status:** PASS
**Elapsed (ms):** 1353.9
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'order_date.day': '2026-10-01T00:00:00.000', 'order_date': '2026-10-01T00:00:00.000', 'orders': '41', 'gross_sales': '77888.19000000003', 'discounts': '2786.4419000000007', 'paid_sales': '94035.00000000001', 'net_sales': '72113.43556659213'}
  {'order_date.day': '2026-10-02T00:00:00.000', 'order_date': '2026-10-02T00:00:00.000', 'orders': '19', 'gross_sales': '39961.14', 'discounts': '1838.0521999999999', 'paid_sales': '47894.09999999999', 'net_sales': '39403.2637092821'}
  {'order_date.day': '2026-10-03T00:00:00.000', 'order_date': '2026-10-03T00:00:00.000', 'orders': '53', 'gross_sales': '106435.76000000005', 'discounts': '4645.681100000001', 'paid_sales': '127372.10000000002', 'net_sales': '104404.1428159976'}
  {'order_date.day': '2026-10-04T00:00:00.000', 'order_date': '2026-10-04T00:00:00.000', 'orders': '48', 'gross_sales': '88551.95', 'discounts': '3426.1032000000005', 'paid_sales': '106810.20000000001', 'net_sales': '80315.91809007902'}
  {'order_date.day': '2026-10-05T00:00:00.000', 'order_date': '2026-10-05T00:00:00.000', 'orders': '52', 'gross_sales': '115709.25000000007', 'discounts': '4781.5265', 'paid_sales': '138563.8', 'net_sales': '110905.60126581544'}
  {'order_date.day': '2026-10-06T00:00:00.000', 'order_date': '2026-10-06T00:00:00.000', 'orders': '56', 'gross_sales': '106371.04000000001', 'discounts': '4175.680800000001', 'paid_sales': '127691.70000000001', 'net_sales': '95471.70818576033'}
  {'order_date.day': '2026-10-07T00:00:00.000', 'order_date': '2026-10-07T00:00:00.000', 'orders': '54', 'gross_sales': '93037.94000000003', 'discounts': '2922.2921000000006', 'paid_sales': '112501.70000000003', 'net_sales': '86621.49838303217'}
  {'order_date.day': '2026-10-08T00:00:00.000', 'order_date': '2026-10-08T00:00:00.000', 'orders': '58', 'gross_sales': '106504.28000000003', 'discounts': '3298.3055', 'paid_sales': '128395.00000000001', 'net_sales': '100399.77273701386'}
  {'order_date.day': '2026-10-09T00:00:00.000', 'order_date': '2026-10-09T00:00:00.000', 'orders': '26', 'gross_sales': '54608.54000000001', 'discounts': '1550.0021', 'paid_sales': '65577', 'net_sales': '53879.72564368781'}
  {'order_date.day': '2026-10-10T00:00:00.000', 'order_date': '2026-10-10T00:00:00.000', 'orders': '27', 'gross_sales': '47586.799999999996', 'discounts': '1850.5089000000003', 'paid_sales': '57583.399999999994', 'net_sales': '47233.324556799336'}

### CM03 [commerce]
**Question:** Which traffic channels generated the most commerce net sales?
**Status:** PASS
**Elapsed (ms):** 1722.9
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'channel': 'ig_feed', 'orders': '245', 'paid_sales': '537174.5999999995', 'net_sales': '422565.72150212293'}
  {'channel': 'fb_feed', 'orders': '69', 'paid_sales': '154739.2', 'net_sales': '125462.90003290166'}
  {'channel': 'google_pmax', 'orders': '62', 'paid_sales': '147060.20000000004', 'net_sales': '116966.05862690293'}
  {'channel': 'google_search', 'orders': '37', 'paid_sales': '90614.8', 'net_sales': '67709.867098827'}
  {'channel': 'unattributed', 'orders': '24', 'paid_sales': '81240.5', 'net_sales': '65333.07503917803'}
  {'channel': 'google_shopping', 'orders': '6', 'paid_sales': '11538.4', 'net_sales': '9778.325555069263'}
  {'channel': 'whatsapp', 'orders': '5', 'paid_sales': '12890', 'net_sales': '8764.403003337042'}
  {'channel': 'meta_organic', 'orders': '1', 'paid_sales': '3096', 'net_sales': '2623.725042979943'}
  {'channel': 'ai_chatgpt', 'orders': '1', 'paid_sales': '2999', 'net_sales': '2541.53'}
  {'channel': 'audience_network', 'orders': '1', 'paid_sales': '2249.1', 'net_sales': '1906.02'}

### CM04 [commerce]
**Question:** How did placed orders and sales differ by payment bucket?
**Status:** PASS
**Elapsed (ms):** 1314.1
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'payment_bucket': 'manual', 'orders': '439', 'cod_orders': '0', 'prepaid_orders': '0', 'paid_sales': '1003298.5999999986', 'net_sales': '793181.9459013187'}
  {'payment_bucket': 'online', 'orders': '7', 'cod_orders': '0', 'prepaid_orders': '7', 'paid_sales': '27567.899999999998', 'net_sales': '21244.840000000004'}
  {'payment_bucket': 'cod', 'orders': '7', 'cod_orders': '7', 'prepaid_orders': '0', 'paid_sales': '16632.4', 'net_sales': '11130.86'}

### CM05 [commerce]
**Question:** How did order count and commerce net sales differ for new and existing customers?
**Status:** PASS
**Elapsed (ms):** 1656.8
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'is_new_customer': '1', 'orders': '347', 'new_customers': '347', 'net_sales': '622388.2045035479'}
  {'is_new_customer': '0', 'orders': '106', 'new_customers': '0', 'net_sales': '203169.44139777182'}

### CM06 [commerce]
**Question:** Which shipping states generated the most commerce net sales?
**Status:** PASS
**Elapsed (ms):** 856.0
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'shipping_state': 'MAHARASHTRA', 'orders': '120', 'net_sales': '214314.23818621424'}
  {'shipping_state': 'KARNATAKA', 'orders': '73', 'net_sales': '152698.09352398335'}
  {'shipping_state': 'DELHI', 'orders': '43', 'net_sales': '68021.4201294021'}
  {'shipping_state': 'HARYANA', 'orders': '35', 'net_sales': '60235.5451260161'}
  {'shipping_state': 'WEST BENGAL', 'orders': '28', 'net_sales': '56209.70005534735'}
  {'shipping_state': 'UTTAR PRADESH', 'orders': '30', 'net_sales': '47363.76182589205'}
  {'shipping_state': 'GUJARAT', 'orders': '17', 'net_sales': '37589.94000468296'}
  {'shipping_state': 'TAMIL NADU', 'orders': '18', 'net_sales': '37285.11591336044'}
  {'shipping_state': 'TELANGANA', 'orders': '16', 'net_sales': '30357.236903485755'}
  {'shipping_state': 'PUNJAB', 'orders': '11', 'net_sales': '22253.579876302843'}

### CM07 [commerce]
**Question:** For September orders, how many were returned or cancelled and what revenue deductions were recorded?
**Status:** PASS
**Elapsed (ms):** 830.1
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'orders': '931', 'returned_orders': '24', 'cancelled_orders': '34', 'returned_or_cancelled_orders': '58', 'return_revenue': '47672.83499999999', 'cancel_revenue': '52807.94000000001'}

### CM08 [commerce]
**Question:** Which Meta campaigns generated the most orders and commerce net sales?
**Status:** PASS
**Elapsed (ms):** 1117.9
**Expected view:** commerce
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'campaign_id': '120250681379400783', 'campaign_name': 'TH-149-SCRATCHLOUNGE-25SEP', 'orders': '26', 'net_sales': '59179.55849539817', 'returned_orders': '0', 'cancelled_orders': '0'}
  {'campaign_id': '120250597947340783', 'campaign_name': 'TH-222-WINGBIRD-23SEP', 'orders': '52', 'net_sales': '58956.32624801519', 'returned_orders': '1', 'cancelled_orders': '0'}
  {'campaign_id': '120250690866980783', 'campaign_name': 'TH-336-PAWTECH-26SEP', 'orders': '29', 'net_sales': '57980.656326444085', 'returned_orders': '0', 'cancelled_orders': '0'}
  {'campaign_id': None, 'campaign_name': None, 'orders': '33', 'net_sales': '56849.97350114388', 'returned_orders': '0', 'cancelled_orders': '0'}
  {'campaign_id': '120250695088250783', 'campaign_name': 'TH-383-SUSPENDER-26SEP-ADV+', 'orders': '20', 'net_sales': '40188.52589905632', 'returned_orders': '2', 'cancelled_orders': '0'}
  {'campaign_id': '120250726752410783', 'campaign_name': 'TH-383-SUSPENDER-29SEP', 'orders': '22', 'net_sales': '40075.80759314254', 'returned_orders': '1', 'cancelled_orders': '2'}
  {'campaign_id': '120250648999490783', 'campaign_name': 'TH-012-SNUGBOO-23SEP', 'orders': '15', 'net_sales': '28560.521044907222', 'returned_orders': '0', 'cancelled_orders': '0'}
  {'campaign_id': '120250823301270783', 'campaign_name': 'TH-149-SCRATCHLOUNGE-6OCT', 'orders': '13', 'net_sales': '27232.812026418047', 'returned_orders': '0', 'cancelled_orders': '1'}
  {'campaign_id': '120250684363180783', 'campaign_name': 'TH-445-PROSUSPENDERBOOTS-25SEP', 'orders': '11', 'net_sales': '26298.50192755955', 'returned_orders': '0', 'cancelled_orders': '0'}
  {'campaign_id': '120250695726350783', 'campaign_name': 'CAT TOY CS-26SEP', 'orders': '16', 'net_sales': '25190.345044617592', 'returned_orders': '0', 'cancelled_orders': '0'}

### CM09 [commerce]
**Question:** Which 10 products generated the most net line revenue and how many units and distinct orders did they have?
**Status:** PASS
**Elapsed (ms):** 672.7
**Expected view:** product
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'product_id': '8240181837913', 'product_title': 'Pawveralls Suspender Boots', 'product_order_net_sales': '122832.36000000007', 'product_net_revenue': '122832.36000000007', 'units_sold': '60', 'product_orders': '62'}
  {'product_id': '8028959539289', 'product_title': 'Meow Scratch Lounge', 'product_order_net_sales': '116267.15000000007', 'product_net_revenue': '116267.15000000007', 'units_sold': '57', 'product_orders': '54'}
  {'product_id': '8152830607449', 'product_title': 'PawTech Cleaner with Paw Cleanser 500ml', 'product_order_net_sales': '73635.56000000003', 'product_net_revenue': '73635.56000000003', 'units_sold': '36', 'product_orders': '37'}
  {'product_id': '8069022580825', 'product_title': 'WingBird Smart Flapping Toy', 'product_order_net_sales': '69903.89999999997', 'product_net_revenue': '69903.89999999997', 'units_sold': '64', 'product_orders': '69'}
  {'product_id': '8349232169049', 'product_title': 'Pawveralls Pro Suspender Boots', 'product_order_net_sales': '46379.8', 'product_net_revenue': '46379.8', 'units_sold': '23', 'product_orders': '27'}
  {'product_id': '8164696162393', 'product_title': 'FusionGrip Harness', 'product_order_net_sales': '29225.639999999996', 'product_net_revenue': '29225.639999999996', 'units_sold': '14', 'product_orders': '14'}
  {'product_id': '7906956968025', 'product_title': 'SnugSole Boots', 'product_order_net_sales': '27063.149999999998', 'product_net_revenue': '27063.149999999998', 'units_sold': '16', 'product_orders': '17'}
  {'product_id': '7960269717593', 'product_title': 'PocketPup Tote Bag (For Small Dogs & Cats) - Up to 6.5kg', 'product_order_net_sales': '22029.429999999997', 'product_net_revenue': '22029.429999999997', 'units_sold': '16', 'product_orders': '16'}
  {'product_id': '0', 'product_title': '', 'product_order_net_sales': '19329.75590131978', 'product_net_revenue': '19329.75590131978', 'units_sold': '0', 'product_orders': '0'}
  {'product_id': '7972300456025', 'product_title': 'WildTrail Boots', 'product_order_net_sales': '12383.4', 'product_net_revenue': '12383.4', 'units_sold': '8', 'product_orders': '8'}
**Warnings:**
  - 'product_net_revenue' is an id of 'product_order_net_sales' (semantic v3: one metric, the date axis is a parameter).

### CM10 [commerce]
**Question:** Which SKUs had the most returned units among September orders?
**Status:** PASS
**Elapsed (ms):** 403.7
**Expected view:** product
**Time basis:** order_date
**Ratio algebra valid:** True
**Grain constraints valid:** True
**Composition identity valid:** True
**Rows (first 10):**
  {'product_id': '8349232169049', 'variant_id': '44574064115801', 'sku': 'TH-445-PROSUSPENDERBOOTS-4XL', 'units_ordered': '13', 'returned_units': '3', 'product_order_return_revenue': '6692.380000000001', 'product_return_revenue': '6692.380000000001'}
  {'product_id': '8069022580825', 'variant_id': '42808195907673', 'sku': 'TH-222-WINGBIRD', 'units_ordered': '115', 'returned_units': '2', 'product_order_return_revenue': '2201.7', 'product_return_revenue': '2201.7'}
  {'product_id': '8349232169049', 'variant_id': '44574064050265', 'sku': 'TH-445-PROSUSPENDERBOOTS-2XL', 'units_ordered': '7', 'returned_units': '1', 'product_order_return_revenue': '2117.8', 'product_return_revenue': '2117.8'}
  {'product_id': '8018108579929', 'variant_id': '42631282557017', 'sku': 'TH-128-FLOORGUARD', 'units_ordered': '2', 'returned_units': '1', 'product_order_return_revenue': '677.12', 'product_return_revenue': '677.12'}
  {'product_id': '8240181837913', 'variant_id': '43391339036761', 'sku': 'TH-383-SUSPENDER-XS-MAROON', 'units_ordered': '10', 'returned_units': '1', 'product_order_return_revenue': '2117.8', 'product_return_revenue': '2117.8'}
  {'product_id': '8350436294745', 'variant_id': '44537887293529', 'sku': 'TH-438-SIPNOVABOTTLE', 'units_ordered': '26', 'returned_units': '1', 'product_order_return_revenue': '592.37', 'product_return_revenue': '592.37'}
  {'product_id': '8396051546201', 'variant_id': '44956131917913', 'sku': 'TH-455-MYSTERYMOTIONMAT', 'units_ordered': '3', 'returned_units': '1', 'product_order_return_revenue': '1270.34', 'product_return_revenue': '1270.34'}
  {'product_id': '8030412603481', 'variant_id': '42681587957849', 'sku': 'TH-162-ROYALLEHENGA-M', 'units_ordered': '0', 'returned_units': '1', 'product_order_return_revenue': '1439.83', 'product_return_revenue': '1439.83'}
  {'product_id': '8027664285785', 'variant_id': '43083512479833', 'sku': 'TH-152-TANGLELEASH-BLACK', 'units_ordered': '37', 'returned_units': '1', 'product_order_return_revenue': '1906.02', 'product_return_revenue': '1906.02'}
  {'product_id': '8082319278169', 'variant_id': '42848888684633', 'sku': 'TH-240-ZOOMICHASETOY', 'units_ordered': '2', 'returned_units': '1', 'product_order_return_revenue': '1143.31', 'product_return_revenue': '1143.31'}
**Warnings:**
  - 'product_return_revenue' is an id of 'product_order_return_revenue' (semantic v3: one metric, the date axis is a parameter).
