from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import requests
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="GitHub Insights", layout="centered")
st.title("GitHub Insights")
st.caption(f"Backend: {BACKEND_URL}")

with st.form("query_form"):
    repo = st.text_input("Repository (owner/repo)", value="pandas-dev/pandas")
    col1, col2 = st.columns(2)
    with col1:
        since = st.date_input("Since", value=date.today() - timedelta(days=14))
    with col2:
        until = st.date_input("Until", value=date.today())
    submitted = st.form_submit_button("Analyze")


def _show_error(resp: requests.Response) -> None:
    try:
        detail = resp.json().get("detail", resp.text)
    except ValueError:
        detail = resp.text
    st.error(f"Error {resp.status_code}: {detail}")


def _render_collaboration_graph(edges: list[dict]) -> None:
    """Real author <-> reviewer network from GET /insights/collaboration - a directed edge
    author -> reviewer means "reviewer reviewed one of author's merged PRs", weighted by
    review count. Self-reviews are already excluded server-side."""
    # cdn_resources="remote": default "local" makes pyvis copy JS/CSS assets into a lib/
    # folder in the current working directory and reference them by relative path, which
    # doesn't resolve once the HTML is embedded via components.html()'s srcdoc iframe.
    net = Network(height="520px", width="100%", directed=True, bgcolor="#ffffff", cdn_resources="remote")

    nodes = {e["author"] for e in edges} | {e["reviewer"] for e in edges}
    for node in nodes:
        net.add_node(node, label=node, size=20)
    for e in edges:
        net.add_edge(
            e["author"],
            e["reviewer"],
            value=e["reviews"],
            title=f"{e['reviewer']} reviewed {e['author']}'s PRs {e['reviews']}x",
            arrows="to",
        )

    # net.set_options() *replaces* pyvis's whole options object (not a merge - see
    # pyvis.options.Options.set), so everything we want has to go in this one call:
    # - nodes.font: per-node font= kwargs get silently dropped by pyvis's Node() internals
    # - interaction: hover tooltips + on-canvas zoom/pan/reset-view buttons
    # - physics.stabilization: lays the graph out, then the injected script below freezes
    #   it - without freezing, barnesHut runs forever and the constant drift makes hovering
    #   a specific edge in a real browser nearly impossible
    net.set_options(
        """
        {
          "nodes": {"font": {"size": 24, "color": "#222222", "strokeWidth": 0}},
          "interaction": {"hover": true, "navigationButtons": true, "keyboard": true, "tooltipDelay": 100},
          "physics": {
            "barnesHut": {"gravitationalConstant": -8000, "springLength": 200, "springConstant": 0.04},
            "minVelocity": 0.75,
            "stabilization": {"iterations": 200}
          }
        }
        """
    )

    tmp_path = tempfile.mktemp(suffix=".html")
    net.save_graph(tmp_path)
    with open(tmp_path, encoding="utf-8") as f:
        html = f.read()
    os.remove(tmp_path)

    freeze_physics_script = """
    <script>
    (function waitForNetwork() {
        if (typeof network !== "undefined" && network) {
            network.once("stabilizationIterationsDone", function () {
                network.setOptions({ physics: false });
            });
        } else {
            setTimeout(waitForNetwork, 100);
        }
    })();
    </script>
    """
    html = html.replace("</body>", freeze_physics_script + "</body>")

    components.html(html, height=560, scrolling=True)


if submitted:
    if until <= since:
        st.error("`Until` must be after `Since`.")
    else:
        params = {"repo": repo, "since": since.isoformat(), "until": until.isoformat()}

        with st.spinner("Fetching contributor data..."):
            try:
                # First call for a given window triggers ingestion, which for an active repo
                # can mean dozens of sequential GitHub API calls (one per merged PR, plus
                # reviews) - generous timeout since there's no caching yet on the first hit.
                contrib_resp = requests.get(f"{BACKEND_URL}/insights/contributors", params=params, timeout=180)
            except requests.RequestException as exc:
                st.error(f"Could not reach the backend at {BACKEND_URL}: {exc}")
                contrib_resp = None

        if contrib_resp is not None:
            if contrib_resp.status_code != 200:
                _show_error(contrib_resp)
            else:
                data = contrib_resp.json()
                st.subheader(f"Top contributors — {data['repo']} ({data['since']} to {data['until']})")
                contributors = data["contributors"]
                if not contributors:
                    st.info("No commits or merged PRs found in this period.")
                else:
                    rows = [
                        {
                            "Login": c["login"],
                            "Commits": c["commits"],
                            "PRs Merged": c["prs_merged"],
                            "Lines Changed": c["lines_changed"],
                        }
                        for c in contributors
                    ]
                    st.dataframe(rows, use_container_width=True, hide_index=True)

                    st.bar_chart(
                        {row["Login"]: row["Commits"] for row in rows},
                        x_label="Contributor",
                        y_label="Commits",
                    )

                    with st.spinner("Fetching reviewer collaboration data..."):
                        try:
                            collab_resp = requests.get(f"{BACKEND_URL}/insights/collaboration", params=params, timeout=60)
                        except requests.RequestException as exc:
                            st.error(f"Could not reach the backend at {BACKEND_URL}: {exc}")
                            collab_resp = None

                    st.subheader("PR reviewer collaboration graph")
                    if collab_resp is not None:
                        if collab_resp.status_code != 200:
                            _show_error(collab_resp)
                        else:
                            edges = collab_resp.json()["edges"]
                            if not edges:
                                st.info("No PR reviews recorded in this period.")
                            else:
                                _render_collaboration_graph(edges)
                                st.caption("Arrow points from PR author to reviewer; hover an edge for the review count.")

                with st.spinner("Generating narrative (calls an LLM, may take a bit)..."):
                    try:
                        narrative_resp = requests.get(f"{BACKEND_URL}/insights/narrative", params=params, timeout=90)
                    except requests.RequestException as exc:
                        st.error(f"Could not reach the backend at {BACKEND_URL}: {exc}")
                        narrative_resp = None

                if narrative_resp is not None:
                    if narrative_resp.status_code != 200:
                        _show_error(narrative_resp)
                    else:
                        n = narrative_resp.json()
                        st.subheader("Narrative")
                        st.write(n["narrative"])
                        if n.get("root_cause_hypothesis"):
                            st.markdown(f"**Root-cause hypothesis:** {n['root_cause_hypothesis']}")
                        st.markdown(f"**Confidence:** {n['confidence']:.0%}")
                        with st.expander("Evidence"):
                            for item in n["evidence"]:
                                st.markdown(f"- {item}")
