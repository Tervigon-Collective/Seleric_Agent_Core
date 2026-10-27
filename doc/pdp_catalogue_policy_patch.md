# PDP catalogue: policy lines that need a jenkins-owned file (catalogue_src/ is not writable by the session user)

Add to `catalogue_src/agent_policy.yaml` under `metrics:` (then copy `direction_bad` onto the metric files, as the file header says):

    pdp_add_to_cart_actions: {direction_bad: down}
    pdp_add_to_cart_page_views: {direction_bad: down}
    pdp_add_to_cart_rate: {direction_bad: down}
    pdp_avg_engaged_seconds: {direction_bad: down}
    pdp_credited_net_revenue: {direction_bad: down}
    pdp_credited_orders: {direction_bad: down}
    pdp_engaged_page_views: {direction_bad: down}
    pdp_engaged_rate: {direction_bad: down}
    pdp_interacted_page_views: {direction_bad: down}
    pdp_interactions_leading_to_cart: {direction_bad: down}
    pdp_purchase_rate: {direction_bad: down}
    pdp_purchased_page_views: {direction_bad: down}
    section_avg_qualified_seconds: {direction_bad: up}
    section_avg_visible_seconds: {direction_bad: down}
    section_interacted: {direction_bad: down}
    section_reach_rate: {direction_bad: down}
    section_reached: {direction_bad: down}

Add to `catalogue_src/causal_priors.yaml` under `view_tiers:` (web layer, same as web_sessions):

    pdp_visits: 4
    pdp_section_reach: 4
    pdp_interaction_log: 4
    listing_card_events: 4

Until then the dependency-graph builder warns "no tier" for the four views and the agent treats PDP metrics as "up is bad".
