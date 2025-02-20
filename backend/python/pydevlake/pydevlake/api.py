# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import annotations

from typing import Optional, Union
from urllib.parse import urljoin
from http import HTTPStatus
import json
import time

import requests as req

from pydevlake import logger


RouteArgs = Union[list[str], dict[str, str]]
QueryArgs = dict[str, str]
Headers = dict[str, str]


class Request:
    def __init__(self,
                 url: str,
                 query_args: Optional[QueryArgs] = None,
                 headers: Optional[Headers] = None):
        self.url = url
        self.query_args = query_args or {}
        self.headers = headers or {}

    def copy(self):
        return Request(self.url, self.query_args, self.headers)

    def __str__(self):
        if self.query_args:
            query_str = '&'.join(f'{k}={v}' for k, v in self.query_args.items())
            return f'{self.url}?{query_str}'
        return self.url


class Response:
    def __init__(self,
                 request: Request,
                 status: int,
                 body: bytes = None,
                 headers: Headers = None):
        self.request = request
        self.status = status
        self.body = body or bytes()
        self.headers = headers or {}

    @property
    def json(self):
        if not hasattr(self, '_json'):
            self._json = json.loads(self.body)
        return self._json

    def __str__(self):
        return f'{self.request}: {self.status}'


# Sentinel value to abort processing of requests/responses in hooks
ABORT = object()


class APIBase:
    """
    The base class for defining APIs.
    It implements a hook system to preprocess requests before sending them and postprocess response
    before returning them.
    Hooks are declared by decorating methods with `@request_hook` and `@response_hook`.
    Hooks are executed in the order they are declared.
    """
    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = req.Session()
        return self._session

    @property
    def base_url(self):
        pass

    def send(self, request: Request):
        request = self._apply_hooks(request, self.request_hooks())
        if request is ABORT:
            return ABORT

        res = self.session.get(
            url=request.url,
            headers=request.headers,
            params=request.query_args
        )

        response = Response(
            request=request,
            status=res.status_code,
            body=res.content,
            headers=res.headers
        )

        response = self._apply_hooks(response, self.response_hooks())

        return response

    def _apply_hooks(self, target, hooks):
        for hook in hooks:
            result = hook.apply(target, self)
            if result is ABORT:
                return ABORT

            if isinstance(result, type(target)):
                target = result
        return target

    def get(self, path, **query_args):
        req = Request(urljoin(self.base_url, path), query_args)
        return self.send(req)

    def request_hooks(self):
        if not hasattr(self, '_request_hooks'):
            self._request_hooks = [h for h in self._iter_members() if isinstance(h, RequestHook)]
        return self._request_hooks

    def response_hooks(self):
        if not hasattr(self, '_response_hooks'):
            self._response_hooks = [h for h in self._iter_members() if isinstance(h, ResponseHook)]
        return self._response_hooks

    def _iter_members(self):
        for c in reversed(type(self).__mro__):
            for m in c.__dict__.values():
                yield m


class RequestHook:
    """
    Preprocess a request before sending it.
    """
    def apply(self, request: Request, api: APIBase):
        pass


class CustomRequestHook(RequestHook):
    def __init__(self, fn):
        self.fn = fn

    def apply(self, request: Request, api: APIBase):
        return self.fn(api, request)

request_hook = CustomRequestHook


class ResponseHook:
    def apply(self, response: Response, api: APIBase):
        pass


class CustomResponseHook(ResponseHook):
    def __init__(self, fn):
        self.fn = fn

    def apply(self, response: Response, api: APIBase):
        return self.fn(api, response)

response_hook = CustomResponseHook


class Paginator:
    """
    Encapsulate logic for handling paginated responses.
    """
    def get_items(self, response) -> Optional[list[object]]:
        """
        Extracts the items from a response, e.g. returning the
        `items` attribute of a JSON body.
        Returning None indicates that the response is not paginated.
        """
        pass

    def get_next_page_id(self, response) -> Optional[int | str]:
        """
        Extracts or compute the id of the next page from the response,
        e.g. incrementing the value of `page` of a JSON body.
        This id will be suplied to the next request via `set_next_page_param`.
        Returning None indicates that the response is the last page.
        """
        pass

    def set_next_page_param(self, request, next_page_id: int | str):
        """
        Modify the request to set the parameter for fetching next page,
        e.g. set the `page` query parameter.
        """
        pass


class PagedResponse(Response):
    """
    Decorate requests.Response to add iteration of items
    within the page and fetching next pages.
    """
    def __init__(self, response, paginator, api):
        self.response = response
        self.paginator = paginator
        self.api = api

    @property
    def items(self):
        return self.paginator.get_items(self.response)

    @property
    def next_page_request(self):
        next_page_id = self.paginator.get_next_page_id(self.response)
        if not next_page_id:
            # No next page
            return None

        next_request = self.response.request.copy()
        self.paginator.set_next_page_param(next_request, next_page_id)
        return next_request

    def __iter__(self):
        """
        Iterate over
        """
        current = self

        while True:
            yield from current.items

            next_page_request = current.next_page_request
            if not next_page_request:
                # No next page
                return

            current = current.api.send(current.next_page_request)

    def __getattr__(self, attr_name):
        # Delegate everything to Response
        return getattr(self.response, attr_name)


class TokenPaginator(Paginator):
    def __init__(self, items_attr: str, next_page_token_attr: str, next_page_token_param: str):
        self.items_attr = items_attr
        self.next_page_token_attr = next_page_token_attr
        self.next_page_token_param = next_page_token_param

    def get_items(self, response) -> Optional[list[object]]:
        return response.json[self.items_attr]

    def get_next_page_id(self, response) -> Optional[int | str]:
        return response.json[self.next_page_token_attr]

    def set_next_page_param(self, request, next_page_id):
        request.query_args[self.next_page_token_param] = next_page_id


class APIException(Exception):
    def __init__(self, response):
        self.response = response

    def __str__(self):
        return f'APIException: {self.response}'


class API(APIBase):
    """
    Provides hooks for:
    - pagination: define the `paginator` property in subclasses

    # TODO:
    - Error handling response hook: retries,
    - Rate limitation
    """
    @property
    def paginator(self) -> Paginator:
        """
        Redefine in subclass to handle pagination
        """
        return None

    @response_hook
    def pause_if_too_many_requests(self, response: Response):
        """
        Pause execution if a response has a 429 status TOO_MANY_REQUEST.
        for the number of seconds indicated in the 'Retry-After' header,
        or 60 seconds if this header is missing.
        Retry the failed request afterwards.
        """
        if response.status == HTTPStatus.TOO_MANY_REQUESTS:
            retry_after = response.headers.get('Retry-After', 60)
            logger.warning(f'Got TOO_MANY_REQUESTS response, sleep {int(retry_after)} seconds')
            time.sleep(retry_after)
            return self.send(response.request)
        return response

    @response_hook
    def handle_error(self, response):
        if response.status != HTTPStatus.OK:
            raise APIException(response)

    @response_hook
    def paginate(self, response):
        paginator = self.paginator
        if not paginator:
            return
        return PagedResponse(response, paginator, self)
