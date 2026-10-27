"""Apply the PDP policy lines from pdp_catalogue_policy_patch.md. Run as the file owner:
sudo -u jenkins python3 /opt/seleric/Seleric_Agent_Core/doc/apply_pdp_policy.py
Idempotent; repairs the line a mangled paste left in causal_priors.yaml."""
import shutil
import sys
import yaml

SRC = "/opt/seleric/Seleric_Agent_Core/catalogue_src/"
DOWN = """pdp_add_to_cart_actions pdp_add_to_cart_page_views pdp_add_to_cart_rate pdp_avg_engaged_seconds
pdp_credited_net_revenue pdp_credited_orders pdp_engaged_page_views pdp_engaged_rate pdp_interacted_page_views
pdp_interactions_leading_to_cart pdp_purchase_rate pdp_purchased_page_views section_avg_visible_seconds
section_interacted section_reach_rate section_reached
listing_buy_now_clicks listing_interactions pdp_interaction_page_views pdp_interactions pdp_page_views
pdp_snapshot_page_views section_exposed""".split()
UP = ["section_avg_qualified_seconds"]
TIERS = ["pdp_visits", "pdp_section_reach", "pdp_interaction_log", "listing_card_events"]

for f in ("agent_policy.yaml", "causal_priors.yaml"):
    shutil.copy(SRC + f, "/tmp/" + f + ".bak")

# causal_priors: drop any earlier pdp lines (incl. the mangled one), then insert the four after web_sessions
lines = [l for l in open(SRC + "causal_priors.yaml").read().split("\n")
         if not l.strip().startswith(tuple(t + ":" for t in TIERS))]
i = lines.index("  web_sessions: 4")
lines[i + 1:i + 1] = ["  %s: 4" % t for t in TIERS]
open(SRC + "causal_priors.yaml", "w").write("\n".join(lines))

# agent_policy: append the missing metric entries
text = open(SRC + "agent_policy.yaml").read()
have = yaml.safe_load(text)["metrics"]
if not text.endswith("\n"):
    text += "\n"
for names, d in ((DOWN, "down"), (UP, "up")):
    for m in names:
        if m not in have:
            text += "  %s:\n    direction_bad: %s\n" % (m, d)
open(SRC + "agent_policy.yaml", "w").write(text)

p = yaml.safe_load(open(SRC + "agent_policy.yaml"))["metrics"]
t = yaml.safe_load(open(SRC + "causal_priors.yaml"))["view_tiers"]
n = len([k for k in DOWN + UP if k in p])
ok = n == 24 and all(t.get(v) == 4 for v in TIERS)
print("policy entries: %d (expect 24); tiers: %s -> %s" % (n, [t.get(v) for v in TIERS], "OK" if ok else "FAIL"))
sys.exit(0 if ok else 1)
