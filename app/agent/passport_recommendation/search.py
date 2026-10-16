"""复用正式资源 HNSW 搜索，按已有通行证聚合候选。"""

from app.agent.passport_recommendation.state import PassportGroup, PassportGroupFile, PassportRecommendationInput
from app.config.passport_recommendation import PassportRecommendationSettings
from app.model.passport import normalize_passports
from app.service.resource_candidates import ReadyResourceSearch


class PassportGroupSearch:
    def __init__(self, resources: ReadyResourceSearch, *, settings: PassportRecommendationSettings):
        self.resources = resources
        self.settings = settings

    async def search(self, file: PassportRecommendationInput) -> list[PassportGroup]:
        file = PassportRecommendationInput.model_validate(file)
        candidates = await self.resources.search_all_platforms(
            file.abstract_embedding, top_k=self.settings.top_k, min_similarity=self.settings.min_similarity,
        )
        grouped: dict[str, list[PassportGroupFile]] = {}
        for candidate in candidates:
            passports = candidate.get("passport")
            if not passports:
                continue
            item = PassportGroupFile.model_validate({key: candidate[key] for key in (
                "resource_id", "source_platform", "name", "abstract", "similarity",
            )})
            # 一份文件可参与多个通行证分组，同组内只保留一次该文件。
            for passport in dict.fromkeys(normalize_passports(passports)):
                grouped.setdefault(passport, []).append(item)
        groups = [PassportGroup(passport=passport, files=sorted(files, key=lambda item: (-item.similarity, item.resource_id)))
                  for passport, files in grouped.items()]
        # 搜索和分组共用已有相似度，不再在 Python 中另算一次向量距离。
        return sorted(groups, key=lambda group: (-group.files[0].similarity, group.passport))
