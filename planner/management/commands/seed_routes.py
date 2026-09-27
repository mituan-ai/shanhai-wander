"""Load the maintained inspiration catalog without changing user itineraries."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from planner.models import RouteTemplate


class Command(BaseCommand):
    help = "幂等导入内置旅行路线；只按模板 slug 更新，不修改用户行程。"

    def handle(self, *args, **options):
        path = Path(__file__).resolve().parents[2] / "data" / "routes.json"
        try:
            routes = json.loads(path.read_text(encoding="utf-8"))
            slugs = [route["slug"] for route in routes]
            if len(slugs) != len(set(slugs)):
                raise ValueError("路线 slug 重复")
            for route in routes:
                if not route["stops"] or route["days"] < 1:
                    raise ValueError(f"路线缺少有效天数或节点：{route['slug']}")
                if any(not 1 <= stop["day"] <= route["days"] for stop in route["stops"]):
                    raise ValueError(f"节点天数超出行程：{route['slug']}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError(f"无法读取路线目录：{exc}") from exc

        created_count = 0
        with transaction.atomic():
            for route in routes:
                defaults = {key: value for key, value in route.items() if key != "slug"}
                _, created = RouteTemplate.objects.update_or_create(
                    slug=route["slug"], defaults=defaults
                )
                created_count += int(created)
        self.stdout.write(self.style.SUCCESS(
            f"路线库已就绪：新增 {created_count} 条，更新 {len(routes) - created_count} 条。"
        ))
