"""Gmail transport.

Isolated behind ``app.ingestion.base`` so nothing in the negotiation engine imports it,
and behind an optional dependency group so the core application, its tests and the
replay harness all run without the Google client libraries installed.
"""
