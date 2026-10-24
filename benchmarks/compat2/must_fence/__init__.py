"""Must-fence adversarial corpus across support tiers T0-T3."""

__all__ = ["evaluate_case", "load_cases", "run_must_fence"]


def __getattr__(name: str):
    if name in __all__:
        from .runner import evaluate_case, load_cases, run_must_fence

        mapping = {
            "evaluate_case": evaluate_case,
            "load_cases": load_cases,
            "run_must_fence": run_must_fence,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
