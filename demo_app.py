"""Demo application for cjm-fasthtml-workflow-session-management.

Standalone session manager that operates on a real `workflow_state.db` copy
from the decomposition workflow host. Demonstrates listing, creating,
renaming, deleting, and resuming sessions.

Run with: python demo_app.py
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fasthtml.common import (
    fast_app, APIRouter, Div, H1, P,
)

# DaisyUI + theming
from cjm_fasthtml_daisyui.core.resources import get_daisyui_headers
from cjm_fasthtml_daisyui.core.testing import create_theme_persistence_script

# App core
from cjm_fasthtml_app_core.core.routing import register_routes
from cjm_fasthtml_app_core.core.htmx import handle_htmx_request

# State store + session helpers
from cjm_workflow_state.state_store import SQLiteWorkflowStateStore, SessionSummary
from cjm_fasthtml_interactions.core.state_store import get_session_id

# Session manager library
from cjm_fasthtml_workflow_session_management.services.management import (
    SessionManagementService,
)
from cjm_fasthtml_workflow_session_management.routes.init import (
    init_session_manager_routers,
)
from cjm_fasthtml_workflow_session_management.models import ColumnSpec
from cjm_fasthtml_workflow_session_management.utils import default_label


# =============================================================================
# Configuration
# =============================================================================

APP_ID = "wsmgmt"

# The fixture is the "golden" snapshot of a real decomposition workflow state DB.
# It is version-controlled and must stay pristine so notebook tests have a
# reproducible baseline. The demo app operates on a *working copy* alongside it
# so user interaction (create/rename/delete) doesn't mutate the fixture.
FIXTURE_DB = Path(__file__).parent / "test_files" / "workflow_state.db"
STATE_DB = Path(__file__).parent / "test_files" / "workflow_state_demo.db"

# The flow ID this demo manages — must match what a real decomp host would use.
FLOW_ID = "structure_decomposition"

# Where the "Resume" action should redirect. In the demo there's no workflow,
# so we point back at the session manager page.
WORKFLOW_URL = "/"


# =============================================================================
# Host enricher + label generator
#
# These mirror the shape a real decomp host would provide. They look for
# fields in state_json that the decomp workflow actually persists (sources,
# segments, media paths) and fall back gracefully if they aren't present.
# =============================================================================

def decomp_enricher(state_json: Dict[str, Any]) -> Dict[str, str]:
    """Turn raw decomposition state into display columns."""
    step_states = state_json.get("step_states", {})
    sources = step_states.get("selection", {}).get("selected_sources", []) or []
    # Decomp's segmentation step used to be under "decomposition" — check both.
    seg_state = step_states.get("segmentation", {}) or step_states.get("decomposition", {}) or {}
    segments = seg_state.get("segments", []) or []
    return {
        "sources": str(len(sources)) if sources else "—",
        "segments": str(len(segments)) if segments else "—",
    }


def decomp_label_generator(summary: SessionSummary, state_json: Dict[str, Any]) -> str:
    """Derive a default label from the first selected source's media path."""
    sources = state_json.get("step_states", {}).get("selection", {}).get("selected_sources", []) or []
    if sources:
        first = sources[0]
        media = first.get("media_path") or first.get("label") or ""
        if media:
            return Path(media).stem
    return default_label(summary.created_at)


# Decomp step ID → display title mapper.
STEP_TITLES = {
    "selection": "Selection",
    "decomposition": "Segment & Align",
    "segmentation": "Segment & Align",
    "review": "Review",
    "verify": "Verify",
}


# =============================================================================
# Main application
# =============================================================================

def main():
    """Initialize the session manager demo and return the FastHTML app."""
    print("\n" + "=" * 70)
    print("Initializing cjm-fasthtml-workflow-session-management Demo")
    print("=" * 70)

    app, rt = fast_app(
        pico=False,
        hdrs=[*get_daisyui_headers(), create_theme_persistence_script()],
        title="Workflow Session Manager Demo",
        htmlkw={'data-theme': 'light'},
        session_cookie=f'session_{APP_ID}_',
        secret_key=f'{APP_ID}-demo-secret',
    )

    router = APIRouter(prefix="")

    # -------------------------------------------------------------------------
    # State store + service
    # -------------------------------------------------------------------------
    print(f"\n[State Store]")
    print(f"  Fixture:     {FIXTURE_DB}")
    print(f"  Working DB:  {STATE_DB}")

    STATE_DB.parent.mkdir(parents=True, exist_ok=True)

    # Seed the working DB from the fixture on first run. Subsequent runs reuse
    # the existing working DB so demo state persists across restarts. Delete
    # the working DB to reset to the pristine fixture.
    if not STATE_DB.exists():
        if FIXTURE_DB.exists():
            print(f"  Seeding working DB from fixture (first run)")
            shutil.copy2(FIXTURE_DB, STATE_DB)
        else:
            print(f"  WARNING: fixture not found — starting with an empty DB")
    else:
        print(f"  Working DB exists, reusing")

    state_store = SQLiteWorkflowStateStore(STATE_DB)

    service = SessionManagementService(
        state_store=state_store,
        flow_id=FLOW_ID,
        enricher=decomp_enricher,
        label_generator=decomp_label_generator,
    )

    # Sanity check: list what's in the fixture.
    existing = service.list_sessions()
    print(f"\n[Service]")
    print(f"  Flow: {FLOW_ID}")
    print(f"  Existing sessions: {len(existing)}")
    for s in existing:
        print(f"    {s.summary.session_id[:8]}... label={s.resolved_label!r} "
              f"step={s.summary.current_step!r} enriched={s.enriched_fields}")

    # -------------------------------------------------------------------------
    # Session manager routers
    # -------------------------------------------------------------------------
    mgmt_result = init_session_manager_routers(
        service=service,
        workflow_url=WORKFLOW_URL,
        prefix="/manage/sessions",
        column_specs=[
            ColumnSpec(field="sources", header="Sources"),
            ColumnSpec(field="segments", header="Segments"),
        ],
        get_step_title=lambda sid: STEP_TITLES.get(sid, sid),
        page_title="Workflow Sessions",
        page_icon="layers",
        tab_entries=[
            ("sessions", "Sessions", "layers", "/manage/sessions/management_page"),
        ],
    )

    print(f"\n[Session Manager URLs]")
    print(f"  management_page: {mgmt_result.urls.management_page}")
    print(f"  create_session:  {mgmt_result.urls.create_session}")
    print(f"  delete_session:  {mgmt_result.urls.delete_session}")
    print(f"  rename_session:  {mgmt_result.urls.rename_session}")
    print(f"  resume_session:  {mgmt_result.urls.resume_session}")

    # -------------------------------------------------------------------------
    # Demo-only root route (redirects to the manager page)
    # -------------------------------------------------------------------------
    @router
    async def index(request):
        """Homepage — loads the session manager page."""
        # Priming the "active session" pointer from the HTTP session so the
        # list can draw the Active badge on the right row. The refresh_items
        # callback in the session router will do this on every mutation, but
        # for the initial GET we wire it manually via the render pipeline.
        sess = request.session
        # Ensure a session ID exists so the badge has something to highlight.
        get_session_id(sess)
        mgmt_result.refresh_items()
        return handle_htmx_request(request, mgmt_result.render_page)

    # -------------------------------------------------------------------------
    # Register routes
    # -------------------------------------------------------------------------
    register_routes(app, router)
    for r in mgmt_result.routers:
        register_routes(app, r)

    print("\n" + "=" * 70)
    print("Registered Routes:")
    print("=" * 70)
    for route in app.routes:
        if hasattr(route, 'path'):
            print(f"  {route.path}")
    print("=" * 70)
    print("Demo App Ready!")
    print("=" * 70 + "\n")

    return app


if __name__ == "__main__":
    import uvicorn
    import webbrowser
    import threading

    app = main()

    port = 5037
    host = "0.0.0.0"
    display_host = 'localhost' if host in ('0.0.0.0', '127.0.0.1') else host

    print(f"Server: http://{display_host}:{port}")
    print()

    timer = threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}"))
    timer.daemon = True
    timer.start()

    uvicorn.run(app, host=host, port=port)
