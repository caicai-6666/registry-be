"""自动入库与手动重荐共用的推荐调用层，不负责审核存储或消息确认。"""

from dataclasses import replace

from langgraph.graph.state import CompiledStateGraph

from app.agent.passport_recommendation import (
    PassportRecommendationInput, PassportRecommendationResult, build_passport_recommendation_workflow,
)
from app.agent.passport_recommendation.context import PassportRecommendationContext
from app.model.pdf import PreparedPDF
from app.service.resource_pdf import ResourcePDFService


class PassportRecommendationService:
    def __init__(self, context: PassportRecommendationContext, pdf_service: ResourcePDFService,
                 *, workflow: CompiledStateGraph | None = None):
        self.context = context
        self.pdf_service = pdf_service
        self.workflow = workflow if workflow is not None else build_passport_recommendation_workflow()

    async def recommend(self, file: PassportRecommendationInput, content: bytes, *, platform_code: str,
                        file_name: str, current_pdf: PreparedPDF | None = None
                        ) -> tuple[PassportRecommendationResult, PreparedPDF | None]:
        file = PassportRecommendationInput.model_validate(file)

        async def prepare_current_pdf():
            nonlocal current_pdf
            # 无候选时不渲染；自动入库可复用判重已准备的页面，每次调用独立保存 PDF。
            if current_pdf is None:
                current_pdf = await self.pdf_service.prepare(content, platform_code=platform_code, file_name=file_name)
            return current_pdf

        context = replace(self.context, prepare_pdf=prepare_current_pdf)
        output = await self.workflow.ainvoke({"file": file, "current_pdf": current_pdf}, context=context)
        return PassportRecommendationResult.model_validate(output), current_pdf
