from enum import Enum


class DocumentTaggedStatus(str, Enum):
   

    AUTO_TAGGED = "Auto-Tagged"
    REVIEW_PENDING = "Review Pending"
    REVIEWED = "Reviewed"
    MANUALLY_TAGGED = "Manually Tagged"

    @classmethod
    def backend_values(cls) -> set[str]:
        """Values the backend is allowed to write. Human values are excluded."""
        return {cls.AUTO_TAGGED.value, cls.REVIEW_PENDING.value}

    @classmethod
    def human_values(cls) -> set[str]:
        """Values owned by the human side. Backend must never overwrite these."""
        return {cls.REVIEWED.value, cls.MANUALLY_TAGGED.value}

    @classmethod
    def all_valid(cls) -> set[str]:
        """All valid choice strings as defined in the SP column."""
        return {m.value for m in cls}


class DeletionStatus(str, Enum):

    NOT_DELETED = "Not Deleted"
    DELETION_REVISED = "Deletion Revised"
    DELETION_APPROVED = "Deletion Approved"
    DELETED = "Deleted"

    @classmethod
    def all_valid(cls) -> set[str]:
        """All valid choice strings as defined in the SP column."""
        return {m.value for m in cls}


def validate_choice_value(
    value: str, enum_class: type[DocumentTaggedStatus | DeletionStatus]
) -> None:
    
    valid = enum_class.all_valid()
    if value not in valid:
        raise ValueError(
            f"Invalid choice value {value!r} for {enum_class.__name__}. "
            f"Allowed values (case-sensitive): {sorted(valid)}"
        )
