"""Key and dial drawing. Pure Pillow — no GTK, no StreamController, no network.

Renderers return images and never touch an action, which is what keeps them
unit-testable and safe to call from a worker thread.
"""
