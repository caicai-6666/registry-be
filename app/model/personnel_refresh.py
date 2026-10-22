"""已有员工资料刷新、数据库变更计划和结果契约。"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmployeeRefreshTarget(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')
    userid: str = Field(strict=True,min_length=1,max_length=128)
    name: str | None = Field(default=None,strict=True,min_length=1,max_length=200)
    department_id: int = Field(strict=True,ge=0)


class DingTalkEmployee(BaseModel):
    userid: str = Field(strict=True,min_length=1,max_length=128)
    name: str = Field(strict=True,min_length=1,max_length=200)
    dept_id_list: list[Annotated[int,Field(strict=True,gt=0)]] = Field(min_length=1)

    @field_validator('userid','name')
    @classmethod
    def reject_blank(cls,value):
        if not value.strip() or value != value.strip():
            raise ValueError('员工标识和姓名不能为空或含首尾空白')
        return value


class DingTalkDepartment(BaseModel):
    dept_id: int = Field(strict=True,gt=0)
    parent_id: int = Field(strict=True,ge=0)
    name: str = Field(strict=True,min_length=1,max_length=200)

    @field_validator('name')
    @classmethod
    def reject_blank(cls,value):
        if not value.strip():raise ValueError('部门名称不能为空')
        return value


class EmployeeRefreshChange(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')
    before: EmployeeRefreshTarget
    name: str = Field(strict=True,min_length=1,max_length=200)
    department_id: int = Field(strict=True,gt=0)


class PersonnelRefreshResult(BaseModel):
    refreshed_users: int = Field(ge=0,description='本次成功读取并完成数据库更新阶段的已有员工数。')
    refreshed_departments: int = Field(ge=0,description='本次刷新名称的所属顶级部门数。')
    synced_names: int = Field(ge=0,description='本次已获得中间件确认的姓名同步数，包含历史待重试项。')
    pending_names: int = Field(ge=0,description='仍待确认的姓名同步项数，成功响应时为零。')


class PersonnelRefreshError(RuntimeError):
    def __init__(self,message: str,*,phase: str,database_updated: bool | None=False,
                 status_code: int=503,pending_names: int | None=None):
        super().__init__(message)
        self.phase,self.database_updated,self.status_code=phase,database_updated,status_code
        self.pending_names=pending_names
