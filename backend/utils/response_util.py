from typing import Optional, Any, Dict
from datetime import datetime

from fastapi.responses import JSONResponse, Response
from fastapi.encoders import jsonable_encoder
from fastapi import status

from constants.code_enum import HttpStatusConstant


class ResponseUtil:
    """
    响应工具类
    """

    @classmethod
    def success(
        cls,
        msg: str = '操作成功',
        data: Optional[Any] = None,
        rows: Optional[Any] = None,
        dict_content: Optional[Dict] = None,
    ) -> Response:
        result = {'code': HttpStatusConstant.SUCCESS, 'msg': msg}
        if data is not None:
            result['data'] = data
        if rows is not None:
            result['rows'] = rows
        if dict_content is not None:
            result.update(dict_content)
        result.update({'success': True, 'time': datetime.now()})
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(result),
        )

    @classmethod
    def failure(
        cls,
        msg: str = '操作失败',
        data: Optional[Any] = None,
        rows: Optional[Any] = None,
    ) -> Response:

        result = {'code': HttpStatusConstant.WARN, 'msg': msg}

        if data is not None:
            result['data'] = data
        if rows is not None:
            result['rows'] = rows

        result.update({'success': False, 'time': datetime.now()})

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=jsonable_encoder(result),
        )

    @classmethod
    def unauthorized(
        cls,
        msg: str = '登录信息已过期，访问系统资源失败',
        data: Optional[Any] = None,
        rows: Optional[Any] = None,
    ) -> Response:

        result = {'code': HttpStatusConstant.UNAUTHORIZED, 'msg': msg}

        if data is not None:
            result['data'] = data
        if rows is not None:
            result['rows'] = rows

        result.update({'success': False, 'time': datetime.now()})

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=jsonable_encoder(result),
        )

    @classmethod
    def forbidden(
        cls,
        msg: str = '该用户无此接口权限',
        data: Optional[Any] = None,
        rows: Optional[Any] = None,
    ) -> Response:
        result = {'code': HttpStatusConstant.FORBIDDEN, 'msg': msg}

        if data is not None:
            result['data'] = data
        if rows is not None:
            result['rows'] = rows

        result.update({'success': False, 'time': datetime.now()})

        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=jsonable_encoder(result),
        )

    @classmethod
    def error(
        cls,
        msg: str = '接口异常',
        data: Optional[Any] = None,
        rows: Optional[Any] = None,
    ) -> Response:
        result = {'code': HttpStatusConstant.ERROR, 'msg': msg}

        if data is not None:
            result['data'] = data
        if rows is not None:
            result['rows'] = rows

        result.update({'success': False, 'time': datetime.now()})

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=jsonable_encoder(result),
        )

    @classmethod
    def not_found(
        cls,
        msg: str = '资源不存在',
        data: Optional[Any] = None,
        rows: Optional[Any] = None,
    ) -> Response:
        result = {'code': HttpStatusConstant.NOT_FOUND, 'msg': msg}

        if data is not None:
            result['data'] = data
        if rows is not None:
            result['rows'] = rows

        result.update({'success': False, 'time': datetime.now()})

        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(result),
        )