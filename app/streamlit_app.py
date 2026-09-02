import os
import sys
from pathlib import Path
from datetime import datetime

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure PyTorch is used cleanly
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import streamlit as st
from dotenv import load_dotenv

from securerag.config import Settings
from securerag.retrieval.hybrid import HybridRetriever
from securerag.retrieval.store import load_store
from securerag.security.audit import read_recent_audit_events
from securerag.security.encryption import VectorStoreEncryptor
from securerag.security.rbac import ROLE_POLICY
from securerag.pipeline.graph import SecureRAG

load_dotenv()

# Page configuration
st.set_page_config(
    page_title="SecureRAG — Security-Hardened Enterprise RAG",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Cyber SOC Dark Theme CSS
st.markdown(
    """
    <style>
    /* Global Styles */
    .stApp {
        background-color: #0b0f19;
        color: #f8fafc;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* Header & Badges */
    .hero-title {
        font-size: 2.1rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: #f8fafc;
        margin-bottom: 0.2rem;
    }
    .hero-subtitle {
        font-size: 0.95rem;
        color: #94a3b8;
        margin-bottom: 1.2rem;
    }
    .security-badge {
        display: inline-block;
        font-size: 0.75rem;
        font-weight: 600;
        padding: 0.25rem 0.65rem;
        border-radius: 9999px;
        margin-right: 0.4rem;
        margin-bottom: 0.4rem;
    }
    .badge-purple {
        background-color: rgba(139, 92, 246, 0.18);
        color: #c4b5fd;
        border: 1px solid rgba(139, 92, 246, 0.35);
    }
    .badge-cyan {
        background-color: rgba(6, 182, 212, 0.18);
        color: #06b6d4;
        border: 1px solid rgba(6, 182, 212, 0.35);
    }
    .badge-blue {
        background-color: rgba(59, 130, 246, 0.18);
        color: #93c5fd;
        border: 1px solid rgba(59, 130, 246, 0.35);
    }

    /* Clearance Pills */
    .pill-public {
        background-color: rgba(100, 116, 139, 0.3);
        color: #cbd5e1;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .pill-internal {
        background-color: rgba(59, 130, 246, 0.25);
        color: #93c5fd;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .pill-confidential {
        background-color: rgba(239, 68, 68, 0.25);
        color: #fca5a5;
        border: 1px solid rgba(239, 68, 68, 0.4);
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }

    /* Cards */
    .stat-card {
        background-color: #111827;
        border: 1px solid #243044;
        border-radius: 10px;
        padding: 0.9rem;
        text-align: center;
    }
    .stat-title {
        font-size: 0.72rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.25rem;
    }
    .stat-value {
        font-size: 1.5rem;
        font-weight: 700;
        font-family: monospace;
    }
    .stat-sub {
        font-size: 0.72rem;
        color: #64748b;
        margin-top: 0.2rem;
    }

    /* Response Box */
    .response-card {
        background-color: #111827;
        border: 1px solid #243044;
        border-radius: 12px;
        padding: 1.25rem;
        margin-top: 1rem;
        margin-bottom: 1.5rem;
    }
    .response-text {
        font-size: 1.05rem;
        line-height: 1.6;
        color: #e2e8f0;
    }

    /* Threat Alert */
    .threat-banner {
        background-color: rgba(245, 158, 11, 0.12);
        border: 1px solid rgba(245, 158, 11, 0.5);
        border-radius: 8px;
        padding: 0.85rem 1.1rem;
        margin-bottom: 1rem;
        color: #fed7aa;
    }
    .threat-banner h4 {
        color: #f59e0b;
        margin: 0 0 0.25rem 0;
        font-size: 0.95rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner="Initializing SecureRAG Engine...")
def get_engine():
    """Initializes and caches the SecureRAG pipeline components."""
    settings = Settings()
    encryptor = VectorStoreEncryptor()
    client, collection, encoder, chunks = load_store(
        settings.chroma_dir, settings.data_dir, encryptor
    )
    retriever = HybridRetriever(collection, encoder, chunks, fusion_k=settings.fusion_k)
    engine = SecureRAG(collection, retriever, encryptor, settings.audit_log_path)
    return engine, collection, settings


engine, collection, settings = get_engine()

# Initialize session state for query inputs
if "active_query" not in st.session_state:
    st.session_state.active_query = "What feedback did we get from LiDAR vendors?"
if "active_role" not in st.session_state:
    st.session_state.active_role = "employee"
if "last_result" not in st.session_state:
    st.session_state.last_result = None

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.markdown("### 🛡️ Identity & RBAC")
    st.caption("Select an authenticated persona to enforce role-based access control.")

    roles = list(ROLE_POLICY.keys())
    role_idx = roles.index(st.session_state.active_role) if st.session_state.active_role in roles else 1

    selected_role = st.selectbox(
        "Active Role:",
        roles,
        index=role_idx,
        format_func=lambda r: f"{r.replace('_', ' ').title()}",
    )
    st.session_state.active_role = selected_role

    policy = ROLE_POLICY[selected_role]
    clearance_name = policy["max_clearance"].upper()
    pill_class = f"pill-{policy['max_clearance'].lower()}"

    st.markdown(
        f"""
        <div style="background:#111827; border:1px solid #243044; border-radius:8px; padding:0.75rem; margin-top:0.4rem; margin-bottom:1rem;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.3rem;">
                <span style="font-size:0.75rem; color:#94a3b8;">Max Clearance:</span>
                <span class="{pill_class}">{clearance_name}</span>
            </div>
            <div style="font-size:0.73rem; color:#64748b;">
                Allowed Depts: <code style="color:#38bdf8;">{', '.join(policy['departments'])}</code>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("### ⚡ Demonstration Presets")
    st.caption("Execute predefined attack & clearance test cases:")

    if st.button("🚨 LiDAR Feedback (Poisoned Doc)", use_container_width=True):
        st.session_state.active_query = "What feedback did we get from LiDAR vendors?"
        st.session_state.active_role = "employee"
        st.rerun()

    if st.button("🚫 Financial Memo (Guest - Denied)", use_container_width=True):
        st.session_state.active_query = "What is the company's financial situation and gross margin?"
        st.session_state.active_role = "guest"
        st.rerun()

    if st.button("✅ Financial Memo (Finance Lead - OK)", use_container_width=True):
        st.session_state.active_query = "What is the company's financial situation and gross margin?"
        st.session_state.active_role = "finance_lead"
        st.rerun()

    if st.button("🤖 Robotics Nav Stack (Internal)", use_container_width=True):
        st.session_state.active_query = "What is known about the Sentinel navigation stack and its limitations?"
        st.session_state.active_role = "employee"
        st.rerun()

    st.markdown("---")
    st.markdown("### ⚙️ Pipeline Settings")
    top_k = st.slider("Top-K Chunks:", min_value=1, max_value=10, value=5)
    st.caption(f"Provider: `{os.getenv('LLM_PROVIDER', 'mock')}`")
    st.caption(f"ChromaDB Chunks: `{collection.count()}`")


# --- MAIN INTERFACE ---
col_head1, col_head2 = st.columns([3, 1])
with col_head1:
    st.markdown('<div class="hero-title">SecureRAG</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-subtitle">Production-Style Security-Hardened Enterprise RAG System</div>',
        unsafe_allow_html=True,
    )

with col_head2:
    st.markdown(
        """
        <div style="text-align: right; padding-top: 0.5rem;">
            <span class="security-badge badge-purple">AES-256-GCM</span>
            <span class="security-badge badge-cyan">LangGraph</span>
            <span class="security-badge badge-blue">Dual-Stage RBAC</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Query Input Row
with st.container():
    query_text = st.text_input(
        "Enter your question:",
        value=st.session_state.active_query,
        key="main_query_input",
        placeholder="Query the secure enterprise knowledge base...",
    )
    col_btn, col_empty = st.columns([1, 4])
    with col_btn:
        execute_clicked = st.button("🔍 Execute Query", type="primary", use_container_width=True)

# Run Query on Click or if triggered
if execute_clicked and query_text.strip():
    with st.spinner("Orchestrating LangGraph retrieval & verification pipeline..."):
        try:
            res = engine.query(query_text, user_role=st.session_state.active_role, top_k=top_k)
            st.session_state.last_result = res
        except Exception as e:
            st.error(f"Execution Error: {e}")

result = st.session_state.last_result

# Telemetry Banner
if result:
    st.markdown("---")
    c1, c2, c3, c4, c5 = st.columns(5)
    
    with c1:
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-title">Dense+Sparse Retrieved</div>
                <div class="stat-value">{len(result.get('retrieved', []))}</div>
                <div class="stat-sub">Chroma + BM25</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-title">RBAC Authorized</div>
                <div class="stat-value" style="color:#10b981;">{len(result.get('authorized', []))}</div>
                <div class="stat-sub">AES-GCM Decrypted</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        blocked = result.get('blocked_count', 0)
        color = "#f59e0b" if blocked > 0 else "#64748b"
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-title">RBAC Blocked</div>
                <div class="stat-value" style="color:{color};">{blocked}</div>
                <div class="stat-sub">Zero Leakage</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        flagged = result.get('flagged_count', 0)
        flag_color = "#ef4444" if flagged > 0 else "#10b981"
        flag_text = f"{flagged} Alert" if flagged > 0 else "0 Clean"
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-title">Prompt Injection</div>
                <div class="stat-value" style="color:{flag_color};">{flag_text}</div>
                <div class="stat-sub">{"Quarantined" if flagged > 0 else "Safe Context"}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c5:
        latency = round(result.get('latency_ms', 0.0), 1)
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-title">Pipeline Latency</div>
                <div class="stat-value" style="color:#38bdf8;">{latency} ms</div>
                <div class="stat-sub">Req: {result.get('request_id', '')[:8]}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Prompt injection alert banner if flagged
    if result.get("flagged_count", 0) > 0:
        st.markdown(
            """
            <div class="threat-banner">
                <h4>⚠️ Prompt Injection Neutralized</h4>
                <div>Retrieved document excerpts contained adversarial instruction directives. 
                Content was quarantined inside <code>[UNTRUSTED_DOCUMENT_CONTENT]</code> delimiters to prevent model hijacking.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Generated Answer Display
    st.markdown("### 💬 Grounded LLM Response")
    st.markdown(
        f"""
        <div class="response-card">
            <div class="response-text">{result.get('answer', 'No answer returned.')}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Tabs: Authorized Excerpts and Live Audit Trail
    tab_sources, tab_audit = st.tabs([
        f"📄 Authorized Excerpts ({len(result.get('authorized', []))})",
        "📋 Security Audit Trail (Live)"
    ])

    with tab_sources:
        excerpts = result.get("context_excerpts", [])
        if not excerpts:
            if result.get("blocked_count", 0) > 0:
                st.info(f"All candidate chunks were blocked by RBAC policy for role '{result.get('user_role')}'.")
            else:
                st.info("No documents matched the query.")
        else:
            for idx, doc in enumerate(excerpts):
                is_flagged = doc.get("flagged", False)
                title = f"Excerpt #{idx + 1}: {doc.get('source')} (Dept: {doc.get('department')}, Clearance: {doc.get('clearance')})"
                if is_flagged:
                    title += " ⚠️ [QUARANTINED]"

                with st.expander(title, expanded=(idx == 0)):
                    if is_flagged:
                        st.warning("⚠️ Contains detected prompt injection patterns. Treated strictly as data.")
                    st.code(doc.get("text", ""), language="text")

    with tab_audit:
        events = read_recent_audit_events(settings.audit_log_path, limit=15)
        if not events:
            st.info("No audit events recorded yet.")
        else:
            table_data = []
            for ev in events:
                iso_time = ev.get("timestamp_iso", "")
                time_disp = iso_time[11:19] if len(iso_time) >= 19 else str(ev.get("timestamp"))
                table_data.append({
                    "Timestamp (UTC)": time_disp,
                    "Role": ev.get("user_role"),
                    "Query SHA-256": ev.get("query_hash"),
                    "Retrieved": ev.get("num_retrieved"),
                    "Authorized": ev.get("authorized_count"),
                    "Blocked": ev.get("blocked_count"),
                    "Threats": ev.get("flagged_chunks"),
                    "Latency (ms)": f"{round(ev.get('latency_ms', 0), 1)}",
                    "Status": ev.get("status"),
                })
            st.dataframe(table_data, use_container_width=True)

else:
    st.info("Select a role and execute a query or click one of the presets in the sidebar to run the security pipeline.")

# Footer
st.markdown("---")
st.markdown(
    "<div style='text-align:center; color:#64748b; font-size:0.8rem;'>"
    "SecureRAG Reference Architecture &bull; Streamlit Production Frontend &bull; AES-256-GCM Payload Encryption &bull; Dual-Stage RBAC &bull; Prompt Isolation"
    "</div>",
    unsafe_allow_html=True,
)
