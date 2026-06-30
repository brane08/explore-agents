from __future__ import annotations

import os
import httpx
import streamlit as st

AGENT_APP_URL = os.environ.get("AGENT_APP_URL", "http://localhost:8002")
B2A_APP_URL = os.environ.get("B2A_APP_URL", "http://localhost:8003")


def _post(url: str, payload: dict, timeout: int) -> tuple[dict | None, str | None]:
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), None
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return None, f"Error: {e}"

st.set_page_config(page_title="AgentFlow", layout="wide")
st.title("AgentFlow")

# ---------------------------------------------------------------------------
# Sidebar — agent selector
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Agent")
    agent_view = st.radio(
        "Select agent",
        ["Agent App — Chat", "B2A App — Run Loop"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(f"agent-app: `{AGENT_APP_URL}`")
    st.caption(f"b2a-app:   `{B2A_APP_URL}`")

# ---------------------------------------------------------------------------
# Shared history per view (session state)
# ---------------------------------------------------------------------------
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []
if "loop_results" not in st.session_state:
    st.session_state.loop_results = []

# ---------------------------------------------------------------------------
# View: Agent App — Chat
# ---------------------------------------------------------------------------
if agent_view == "Agent App — Chat":
    st.subheader("Chat")

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    with st.expander("Request options", expanded=False):
        task_desc = st.text_input(
            "Task description",
            value="Analyse log records: search for errors, aggregate by service.",
        )
        required_tools = st.text_input(
            "Required tools (comma-separated)",
            value="search_logs,get_log_stats",
        )

    prompt = st.chat_input("Message")
    if prompt:
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        tools = [t.strip() for t in required_tools.split(",") if t.strip()]
        payload = {
            "task_description": task_desc,
            "message": prompt,
            "required_tools": tools,
        }

        with st.chat_message("assistant"):
            with st.spinner("Calling agent-app…"):
                data, err = _post(f"{AGENT_APP_URL}/chat", payload, timeout=60)
                body = err if err else (
                    f"**Agent:** `{data['agent_name']}` | **Model:** `{data['model']}`\n\n"
                    f"**Tools used:** {', '.join(f'`{t}`' for t in data['tools_used'])}\n\n"
                    f"---\n\n{data['response']}"
                )
            st.markdown(body)

        st.session_state.chat_messages.append({"role": "assistant", "content": body})

    if st.session_state.chat_messages:
        if st.button("Clear chat", key="clear_chat"):
            st.session_state.chat_messages = []
            st.rerun()

# ---------------------------------------------------------------------------
# View: B2A App — Run Loop
# ---------------------------------------------------------------------------
else:
    st.subheader("B2A Eval Loop")

    col1, col2 = st.columns([2, 1])
    with col1:
        task_desc = st.text_area(
            "Task description",
            value="Analyse log records: search for errors, aggregate by service, compute field stats.",
            height=80,
        )
        eval_case_ids_raw = st.text_input(
            "Eval case IDs (comma-separated, leave blank for all)",
            value="",
        )
    with col2:
        max_iterations = st.slider("Max iterations", min_value=1, max_value=20, value=5)
        sandbox_timeout = st.slider("Sandbox timeout (s)", min_value=5, max_value=120, value=30)

    run_clicked = st.button("Run loop", type="primary")

    if run_clicked:
        eval_case_ids = (
            [c.strip() for c in eval_case_ids_raw.split(",") if c.strip()]
            if eval_case_ids_raw.strip()
            else []
        )
        payload: dict = {
            "task_description": task_desc,
            "max_iterations": max_iterations,
            "sandbox_timeout": sandbox_timeout,
        }
        if eval_case_ids:
            payload["eval_case_ids"] = eval_case_ids

        with st.spinner("Running eval loop — this may take a minute…"):
            result, err = _post(f"{B2A_APP_URL}/run-loop", payload, timeout=300)
            if err:
                st.error(err)
            else:
                st.session_state.loop_results.insert(0, result)

    for i, result in enumerate(st.session_state.loop_results):
        label = f"Run #{len(st.session_state.loop_results) - i}"
        with st.expander(
            f"{label} — score {result['final_score']:.2f} | {result['stopped_reason']} | {result['iterations_run']} iter",
            expanded=(i == 0),
        ):
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Final score", f"{result['final_score']:.2%}")
            mc2.metric("Iterations", result["iterations_run"])
            mc3.metric("Stopped", result["stopped_reason"])

            if result.get("trend"):
                st.line_chart(result["trend"], height=120)

            if result.get("case_results"):
                st.markdown("**Case results**")
                for cr in result["case_results"]:
                    icon = "✅" if cr["passed"] else "❌"
                    st.markdown(f"{icon} `{cr['case_id']}` — {cr['detail']}")

            if result.get("final_agent_path"):
                st.caption(f"Agent saved: `{result['final_agent_path']}`")

    if st.session_state.loop_results:
        if st.button("Clear results", key="clear_loop"):
            st.session_state.loop_results = []
            st.rerun()
