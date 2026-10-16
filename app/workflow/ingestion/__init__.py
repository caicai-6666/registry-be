"""入库申请工作流。"""

def build_ingestion_workflow(node_overrides=None, *, interrupt_before_comparison=False):
    # 延迟加载组装器，避免运行依赖与共享状态之间形成循环导入。
    from app.workflow.ingestion.graph import build_ingestion_workflow as build
    return build(node_overrides, interrupt_before_comparison=interrupt_before_comparison)

__all__ = ["build_ingestion_workflow"]
