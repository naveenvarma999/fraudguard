"""Standalone experiment report and measured evaluation figures."""

import html

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import precision_recall_curve


def render_report(report, output, y, p):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    precision, recall, _ = precision_recall_curve(y, p)
    axes[0, 0].plot(recall, precision, color="#167d9a", linewidth=2)
    axes[0, 0].axhline(np.mean(y), linestyle="--", color="#cf7550", label="Prevalence baseline")
    axes[0, 0].set(
        xlabel="Recall", ylabel="Precision", title="Final temporal holdout", ylim=(0, 1.03)
    )
    axes[0, 0].legend()
    actual, predicted = calibration_curve(y, p, n_bins=10, strategy="uniform")
    axes[0, 1].plot([0, 1], [0, 1], "--", color="#aaa")
    axes[0, 1].plot(predicted, actual, "o-", color="#167d9a")
    axes[0, 1].set(
        xlabel="Mean predicted risk",
        ylabel="Observed fraud fraction",
        title="Reliability (sparse bins are noisy)",
    )
    c = report["test"]["confusion"]
    matrix = np.array([[c["tn"], c["fp"]], [c["fn"], c["tp"]]])
    axes[1, 0].imshow(np.log1p(matrix), cmap="Blues")
    for i in range(2):
        for j in range(2):
            axes[1, 0].text(
                j,
                i,
                f"{matrix[i, j]:,}",
                ha="center",
                va="center",
                color="white" if (i == j == 0) else "black",
                fontsize=16,
            )
    axes[1, 0].set(
        xticks=[0, 1],
        xticklabels=["Pass", "Review"],
        yticks=[0, 1],
        yticklabels=["Legitimate", "Fraud"],
        title="Review policy outcomes",
    )
    importance = report["permutation_importance_on_policy"][:10][::-1]
    axes[1, 1].barh(
        [r["feature"] for r in importance],
        [r["ap_drop_mean"] for r in importance],
        xerr=[r["ap_drop_std"] for r in importance],
        color="#167d9a",
    )
    axes[1, 1].set(xlabel="Average precision decrease", title="Global permutation importance")
    fig.suptitle("FraudGuard / measured benchmark results", fontsize=19, fontweight="bold")
    fig.savefig(output / "evaluation.png", dpi=160)
    plt.close(fig)
    m, policy = report["test"], report["policy"]
    trials = "".join(
        f"<tr><td>{html.escape(x['name'])}</td><td>{x['tune_average_precision']:.4f}</td>"
        f"<td>{x['seconds']:.1f}s</td></tr>"
        for x in report["experiments"]
    )
    splits = "".join(
        f"<tr><td>{k}</td><td>{v['rows']:,}</td><td>{v['frauds']}</td>"
        f"<td>{v['time_min']:.0f}–{v['time_max']:.0f}</td></tr>"
        for k, v in report["splits"].items()
    )
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FraudGuard | Evaluation</title>
<style>body{{margin:0;background:#f3f6f8;color:#152c3c;font:16px/1.6 system-ui}}
main{{max-width:1100px;margin:auto;padding:48px 24px}}h1{{font-size:44px;letter-spacing:-2px;margin:0}}
.tag{{color:#167d9a;letter-spacing:2px;font-size:12px;font-weight:700}}.lead{{max-width:800px;color:#526474}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:32px 0}}
.card,section{{background:white;border:1px solid #dbe3e9;border-radius:12px;padding:22px}}
.card b{{display:block;font-size:30px}}.card span{{color:#526474;font-size:13px}}
section{{margin:20px 0}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #e2e8ee}}
img{{width:100%;height:auto}}.note{{border-left:4px solid #d38a3a;padding:16px;background:#fff8eb}}
code{{font-size:13px;overflow-wrap:anywhere}}@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:34px}}}}</style>
<main><div class="tag">FRAUDGUARD / EXPERIMENT REPORT</div><h1>Risk scores, tested forward in time.</h1>
<p class="lead">A reproducible fraud screening benchmark with separate model selection, probability calibration,
policy tuning, and final evaluation windows. Generated from an actual training run.</p>
<div class="cards"><div class="card"><b>{m["average_precision"]:.3f}</b><span>Average precision</span></div>
<div class="card"><b>{m["recall"]:.1%}</b><span>Fraud recall at fixed threshold</span></div>
<div class="card"><b>{m["precision"]:.1%}</b><span>Review precision</span></div>
<div class="card"><b>{m["review_rate"]:.2%}</b><span>Transactions flagged</span></div></div>
<p class="note">Research benchmark, not a live banking validation. The anonymized PCA inputs cannot be produced
from raw card transactions without the original upstream transformation. Two days of data cannot establish long-term reliability.</p>
<section><h2>Measured results</h2><img src="evaluation.png" alt="Precision recall, calibration, confusion matrix, and feature importance">
<p>Final test: {m["rows"]:,} transactions, {m["frauds"]} frauds. ROC AUC: {m["roc_auc"]:.4f}.
Brier score: {m["brier"]:.6f}. AP bootstrap interval: {m["ap_interval"]["low"]:.3f}–{m["ap_interval"]["high"]:.3f}.
This row bootstrap assumes independent observations and fixes prevalence; it does not capture temporal uncertainty.</p></section>
<section><h2>How the decision was made</h2><p>Champion: <strong>{html.escape(report["champion"])}</strong>.
Selection uses tuning-window average precision only. Sigmoid calibration uses the next disjoint window.
Threshold {policy["threshold"]:.6f} minimizes simulated review and missed-fraud cost under a {policy["max_review_rate"]:.1%}
validation review budget. This fixed threshold cannot guarantee the same capacity on future traffic.</p>
<p>Assumed cost: {policy["review_cost"]:g} per review and {policy["missed_fraud_cost"]:g} per missed fraud,
with perfect recovery of reviewed fraud. Test simulated cost: {m["simulated_cost"]:,.0f} versus
{m["no_review_cost"]:,.0f} with no reviews. These are illustrative units, not measured financial savings.</p>
<table><tr><th>Candidate</th><th>Tuning AP</th><th>Fit + score</th></tr>{trials}</table></section>
<section><h2>Data and leakage controls</h2><p>{report["dataset"]["raw_rows"]:,} input rows;
{report["dataset"]["duplicates_removed"]:,} exact duplicates removed; {report["purged_rows"]:,} rows purged near boundaries.
Same timestamps remain together. All preprocessing is fit inside training. Time is used only for splitting.</p>
<table><tr><th>Window</th><th>Rows</th><th>Frauds</th><th>Seconds from start</th></tr>{splits}</table>
<p>Data SHA-256: <code>{report["dataset"]["sha256"]}</code></p></section>
<section><h2>Operational interpretation</h2><p>Global feature importance explains dependence on anonymized components,
not causal reasons for an individual transaction. No protected attributes or customer identifiers are available,
so fairness, repeat-customer leakage, and customer-level generalization remain untested.</p>
<p>Drift monitor alert on test versus training: <strong>{report["test_vs_train_drift"]["alert"]}</strong>.
PSI is a diagnostic heuristic; pair it with delayed labeled performance and review-queue capacity.</p>
<p>See evaluation.json for complete measured results and docs/RUNBOOK.md for deployment and rollback.</p></section>
<footer>Seed {report["seed"]} · {html.escape(report["created_utc"])} · Training and analysis {report["duration_seconds"]:.1f}s</footer></main></html>"""
    (output / "report.html").write_text(page, encoding="utf-8")
