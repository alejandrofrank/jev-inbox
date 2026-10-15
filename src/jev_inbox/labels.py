"""Gmail destination names, independent of the TypeSafe Jev model name."""

JOB_LABEL = "grok"
ALLOWED_LABELS = frozenset(
    {
        JOB_LABEL,
        *(
            f"{JOB_LABEL}/{name}"
            for name in (
                "Applied",
                "Assessment",
                "Interview",
                "Offer",
                "Rejected",
                "Update",
                "Opportunity",
                "Work",
            )
        ),
    }
)
