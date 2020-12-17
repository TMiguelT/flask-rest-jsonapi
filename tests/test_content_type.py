"""
Tests relating to content types, which are independent from the data layer
"""
from csv import DictWriter, DictReader
from io import StringIO
from flask import make_response, Blueprint, Flask, request
from werkzeug.test import EnvironBuilder
import json

import pytest

from flapison import Api, ResourceDetail


@pytest.fixture()
def csv_api(app, api, register_routes):
    bp = Blueprint("api", __name__)
    api = Api(
        blueprint=bp,
        response_renderers={"text/csv": render_csv},
        request_parsers={"text/csv": parse_csv},
    )
    register_routes(api)


def flatten_json(y):
    out = {}

    def flatten(x, name=""):
        if type(x) is dict:
            for a in x:
                flatten(x[a], name + a + ".")
        elif type(x) is list:
            i = 0
            for a in x:
                flatten(a, name + str(i) + ".")
                i += 1
        else:
            out[name[:-1]] = x

    flatten(y)
    return out


def render_csv(response):
    data = response["data"]
    # Treat single values as a list of one element
    if not isinstance(data, list):
        data = [data]

    # Flatten the list of rows
    rows = []
    fields = set()
    for row in data:
        flattened = flatten_json(row)
        rows.append(flattened)
        fields.update(flattened.keys())

    # Write the rows to CSV
    with StringIO() as out:
        writer = DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return make_response(out.getvalue(), 200, {"Content-Type": "text/csv"})


def unflatten_json(obj):
    output = {}
    for key, value in obj.items():
        current_obj = output
        split = key.split(".")
        for i, segment in enumerate(split):
            # If the segment doesn't already exist, create it
            if segment not in current_obj:
                current_obj[segment] = {}

            if i == len(split) - 1:
                # If this is the last item, store it
                current_obj[segment] = value
            else:
                # If this is not the last item, go deeper into the tree
                current_obj = current_obj[segment]
    return output


def parse_csv(request):
    objects = []
    with StringIO(request.data.decode()) as fp:
        reader = DictReader(fp)
        for row in reader:
            objects.append(unflatten_json(row))

    # We only ever have to parse singleton rows
    objects = objects[0]

    return {"data": objects}


def test_csv_response(csv_api, person, person_2, client):
    response = client.get(
        "/persons",
        headers={"Content-Type": "application/vnd.api+json", "Accept": "text/csv"},
    )
    rows = list(DictReader(response.data.decode().split()))

    # Since we used person and person2, there should be 2 rows
    assert len(rows) == 2

    # The names should be in the dictionary
    names = set([row["attributes.name"] for row in rows])
    assert "test" in names
    assert "test2" in names


def test_csv_request(csv_api, client, person_schema):
    with StringIO() as fp:
        writer = DictWriter(fp, fieldnames=["attributes.name", "type"])
        writer.writeheader()
        writer.writerow({"attributes.name": "one", "type": "person"})

        response = client.post(
            "/persons",
            data=fp.getvalue(),
            headers={"Content-Type": "text/csv", "Accept": "application/vnd.api+json"},
        )

    # A new row was created
    assert response.status_code == 201

    # The returned data had the same name we posted
    assert response.json["data"]["attributes"]["name"] == "one"


def test_class_content_types(app, client):
    """
    Test that we can override the content negotiation on a class level
    """

    class TestResource(ResourceDetail):
        response_renderers = {
            "text/fake_content": lambda x: make_response(
                str(x), 200, {"Content-Type": "text/fake_content"}
            )
        }
        request_parsers = {"text/fake_content": str}

        def get(self):
            return "test"

    bp = Blueprint("api", __name__)
    api = Api(blueprint=bp)
    api.route(TestResource, "test", "/test")
    api.init_app(app)

    # If the content negotiation has successfully been changed, a request with a strange
    # content type should work, and return the same type
    rv = client.get(
        "/test",
        headers={"Content-Type": "text/fake_content", "Accept": "text/fake_content"},
    )

    assert rv.status_code == 200
    assert rv.data.decode() == "test"
    assert rv.mimetype == "text/fake_content"


# Test using an encoding argument and a boundary argument
@pytest.mark.parametrize(
    ["content_type", "data"],
    [
        ["text/html; charset=UTF-8", "hello"],
        ["multipart/form-data; boundary=boundary", {"a": "1", "b": "2"}],
    ],
)
def test_content_arguments(api, client, app, content_type, data):
    """
    Test content types with arguments, such as encoding or content boundaries
    """

    class TestResource(ResourceDetail):
        request_parsers = {
            "text/html": str,
            "multipart/form-data": str,
        }

        response_renderers = {
            "application/json": lambda x: make_response(
                json.dumps(x), 200, {"Content-Type": "application/json"}
            )
        }

        def post(self):
            return request.data.decode() or dict(request.form)

    api.route(TestResource, "test", "/test")
    api.init_app(app)

    rv = client.post(
        "/test",
        data=data,
        headers={"Content-Type": content_type, "Accept": "application/json"},
    )

    # Check that each request worked, and returned the correctly-parsed data
    assert rv.status_code == 200
    assert rv.json == data


def test_accept_star(person, person_2, client, registered_routes):
    """
    Check that an Accept: */* header works
    """
    response = client.get(
        "/persons",
        headers={"Content-Type": "application/vnd.api+json", "Accept": "*/*"},
    )
    assert response.status_code == 200, response.json
    assert len(response.json["data"]) == 2


def test_accept_no_accept(person, person_2, client, registered_routes):
    """
    Check that a request without an Accept header works
    """
    response = client.get(
        "/persons",
        headers={
            "Content-Type": "application/vnd.api+json",
        },
    )

    assert response.status_code == 200, response.json
    assert len(response.json["data"]) == 2


def test_no_content_get(person, person_2, client, registered_routes):
    """
    Check that we can still make GET requests without a Content-Type header
    """
    response = client.get("/persons")

    assert response.status_code == 200, response.json
    assert len(response.json["data"]) == 2


def test_no_content_post(person, person_2, client, registered_routes):
    """
    Check that we can't make POST requests without a Content-Type header
    """
    response = client.post("/persons")

    assert response.status_code == 415


def test_accept_charset(person, person_2, client, registered_routes):
    """
    Check that a request with a valid Accept header but a charset parameter works
    """
    response = client.get(
        "/persons",
        headers={
            "Accept": "application/vnd.api+json; charset=utf-8",
        },
    )

    assert response.status_code == 200, response.json
    assert len(response.json["data"]) == 2
