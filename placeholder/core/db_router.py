from dataclasses import dataclass
from typing import Any, Optional, Set

from django.apps import apps
from django.contrib.sessions.models import Session
from django.test.runner import DiscoverRunner


@dataclass
class RoutingRule:
    db_name: str
    labels: Set[str]
    writable: bool = False


class DBRouter(object):
    rules = [
        RoutingRule(
            db_name='prospectus_lumos',
            labels={
                # pass
            }, writable=True),
    ]

    def db_for_read(self, model: Session, **hints: dict) -> str:
        apps_name = model._meta.app_label
        for rule in self.rules:
            if apps_name in rule.labels:
                return rule.db_name
        return 'default'

    def db_for_write(self, model: Session, **hints: dict) -> Optional[str]:
        apps_name = model._meta.app_label
        for rule in self.rules:
            if apps_name in rule.labels:
                return rule.db_name if rule.writable else None
        return 'default'

    def allow_relation(self, obj1: Session, obj2: Session, **hints: dict) -> bool:
        # Obj1 and Obj2 both in the ordering app_labels or both not in labels, OK
        # The rest Not OK
        is_default = True
        for rule in self.rules:
            obj1_in_db = obj1._meta.app_label in rule.labels
            obj2_in_db = obj2._meta.app_label in rule.labels
            if obj1_in_db and obj2_in_db:
                return True
            is_default &= not obj1_in_db and not obj2_in_db
        return is_default

    def allow_migrate(self, db: str, app_label: str, model_name: Optional[str] = None, **hints: dict) -> bool:
        for rule in self.rules:
            if app_label in rule.labels:
                return False
        return db == 'default'


class UnManagedModelTestRunner(DiscoverRunner):
    '''
    https://dev.to/patrnk/testing-against-unmanaged-models-in-django
    '''

    def setup_test_environment(self, *args: Any, **kwargs: Any) -> None:
        get_models = apps.get_models
        self.unmanaged_models = [m for m in get_models() if not m._meta.managed]
        for m in self.unmanaged_models:
            if m._meta.app_label == "django_rq":
                continue

            m._meta.managed = True
            m._meta.db_table = f'{m._meta.app_label}_{m._meta.db_table}'[:63]
        super().setup_test_environment(*args, **kwargs)

    def teardown_test_environment(self, *args: Any, **kwargs: Any) -> None:
        super().teardown_test_environment(*args, **kwargs)
        # reset unmanaged models
        for m in self.unmanaged_models:
            m._meta.managed = False
