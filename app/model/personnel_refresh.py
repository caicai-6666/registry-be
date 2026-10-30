"""已有员工资料刷新、数据库变更计划和结果契约。"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.model.employee import EmployeeWorkProfile


class EmployeeRefreshTarget(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')
    userid: str = Field(strict=True,min_length=1,max_length=128)
    name: str | None = Field(default=None,strict=True,min_length=1,max_length=200)
    department_id: int = Field(strict=True,ge=0)
    department_ids: list[Annotated[int,Field(strict=True,ge=0)]] | None = Field(default=None, min_length=1, description='全部一级部门ID；历史NULL回退单值。')
    work_profile: EmployeeWorkProfile = Field(description='刷新前持久化的任职资料，用于检测并发修改。')


class DingTalkEmployee(BaseModel):
    userid: str = Field(strict=True,min_length=1,max_length=128)
    name: str = Field(strict=True,min_length=1,max_length=200)
    dept_id_list: list[Annotated[int,Field(strict=True,gt=0)]] = Field(min_length=1)
    manager_userid: str | None = Field(default=None,strict=True,max_length=128,
        description='钉钉员工详情中的直属主管企业 userid，未维护时为空。')
    title: str | None = Field(default=None,strict=True,max_length=200,
        description='钉钉员工详情中的职位名称，未维护时为空。')

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

    @model_validator(mode='before')
    @classmethod
    def root_parent(cls, value):
        if isinstance(value,dict) and value.get('dept_id') == 1 and 'parent_id' not in value:
            return {**value,'parent_id':0}
        return value

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
    department_ids: list[Annotated[int,Field(strict=True,ge=0)]] | None = Field(default=None, min_length=1, description='全部一级部门ID；历史NULL回退单值。')
    work_profile: EmployeeWorkProfile = Field(description='本次从钉钉读取并归一化的任职资料。')


class PersonnelRefreshResult(BaseModel):
    refreshed_users: int = Field(ge=0,description='本次成功读取并完成数据库更新阶段的已有员工数。')
    refreshed_departments: int = Field(ge=0,description='本次刷新名称的所属顶级部门数。')
    synced_names: int = Field(ge=0,description='本次已确认姓名和部门同步的员工数，字段名保留兼容，包含历史待重试项。')
    pending_names: int = Field(ge=0,description='仍待确认姓名和部门同步的员工数，字段名保留兼容，成功时为零。')


class PersonnelRefreshError(RuntimeError):
    def __init__(self,message: str,*,phase: str,database_updated: bool | None=False,
                 status_code: int=503,pending_names: int | None=None):
        super().__init__(message)
        self.phase,self.database_updated,self.status_code=phase,database_updated,status_code
        self.pending_names=pending_names
