"""Live smoke check of the Buildings & Locations and Cameras web contract (Phase 7).

It calls the Core API **through the Vite dev server's /api proxy**, with the URLs, methods and bodies
the web pages send (features/locations/api.ts, features/cameras/api.ts). Sign-in is real (codes from
the development email outbox).

ONLY run it against a server that uses an isolated scratch database and EMAIL_DELIVERY_MODE=dev_outbox
(see smoke/README.md). Each run creates two new organizations; nothing existing is changed or removed.

    python3 smoke/site_smoke.py --web http://127.0.0.1:5179 --outbox /path/to/outbox --token <ingest token>

Standard library only. Exit code 0 when every check passes. The data is left in place, so the pages
can be looked at afterwards; the sign-in details are printed at the end.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from operations_smoke import Org, detect
from personnel_smoke import PASSWORD, Api, check, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--token", required=True, help="the API's DETECTION_INGEST_TOKEN (scratch value)")
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir():
        print("Refusing: the development outbox directory does not exist (EMAIL_DELIVERY_MODE=dev_outbox).")
        return 2
    api = Api(args.web)

    a = Org(api, args.outbox, "Site Smoke")
    sup_email = f"sup.{a.run}@smoke.example.com"
    a.create("/organization-admin/supervisors", a.admin, a.admin_email, "Sana Supervisor", sup_email)
    sup = a.sign_in(sup_email)
    g_email = f"g1.{a.run}@smoke.example.com"
    guard = a.create("/guards", a.admin, a.admin_email, "Bilal Guard", g_email)

    # --- Buildings and perimeters: Admins only ---------------------------------------------------------
    s, block = api.call("POST", "/locations/buildings", {"name": "Admin Block", "kind": "INDOOR"}, a.admin)
    check(s == 201 and block["kind"] == "INDOOR", "Admin adds a building (POST /locations/buildings {name, kind})")
    s, fence = api.call("POST", "/locations/buildings", {"name": "North Fence", "kind": "OUTDOOR_PERIMETER"}, a.admin)
    check(s == 201, "Admin adds an outdoor perimeter")
    s, _ = api.call("POST", "/locations/buildings", {"name": "Sup Block", "kind": "INDOOR"}, sup)
    check(s == 403, "Supervisor cannot add a building (403)")
    s, _ = api.call("PUT", f"/locations/buildings/{block['id']}", {"name": "Renamed", "kind": "INDOOR"}, sup)
    check(s == 403, "Supervisor cannot edit a building (403)")
    s, _ = api.call("DELETE", f"/locations/buildings/{fence['id']}", token=sup)
    check(s == 403, "Supervisor cannot remove a perimeter (403)")
    s, renamed = api.call("PUT", f"/locations/buildings/{block['id']}", {"name": "Admin Block A", "kind": "INDOOR"}, a.admin)
    check(s == 200 and renamed["name"] == "Admin Block A", "Admin renames a building (PUT /locations/buildings/{id})")
    s, dup = api.call("POST", "/locations/buildings", {"name": "North Fence", "kind": "INDOOR"}, a.admin)
    check(s == 409 and isinstance(dup.get("detail"), str), "duplicate building name is refused with a message (409)")

    # --- Floors and areas: Admins and Supervisors -------------------------------------------------------
    s, ground = api.call("POST", f"/locations/buildings/{block['id']}/floors", {"name": "Ground", "level": 0}, sup)
    check(s == 201 and ground["level"] == 0, "Supervisor adds a floor (POST …/floors {name, level})")
    s, _ = api.call("POST", f"/locations/buildings/{fence['id']}/floors", {"name": "F1", "level": None}, sup)
    check(s == 422, "a perimeter takes no floors (422)")
    s, floor = api.call("PUT", f"/locations/floors/{ground['id']}", {"name": "Ground Floor", "level": None}, sup)
    check(s == 200 and floor["level"] is None, "Supervisor edits a floor; empty level is null (PUT /locations/floors/{id})")
    body = {"name": "Server Room", "floor_id": ground["id"], "boundary_direction": "NORTH", "boundary_name": None, "boundary_side": "INSIDE"}
    s, room = api.call("POST", f"/locations/buildings/{block['id']}/areas", body, sup)
    check(s == 201 and room["boundary_direction"] == "NORTH" and room["boundary_side"] == "INSIDE",
          "Supervisor adds an area with a boundary (POST …/areas {name, floor_id, boundary_*})")
    body = {"name": "Gate", "floor_id": None, "boundary_direction": "CUSTOM", "boundary_name": "Main gate", "boundary_side": "OUTSIDE"}
    s, gate = api.call("POST", f"/locations/buildings/{fence['id']}/areas", body, a.admin)
    check(s == 201 and gate["boundary_name"] == "Main gate", "custom boundary name on a perimeter area")
    s, _ = api.call("POST", f"/locations/buildings/{fence['id']}/areas",
                    {"name": "X", "floor_id": None, "boundary_direction": "CUSTOM", "boundary_name": None, "boundary_side": None}, a.admin)
    check(s == 422, "a custom boundary without a name is refused (422)")
    s, lobby = api.call("PUT", f"/locations/areas/{room['id']}",
                        {"name": "Server Room", "floor_id": ground["id"], "boundary_direction": "WEST", "boundary_name": None, "boundary_side": None}, sup)
    check(s == 200 and lobby["boundary_direction"] == "WEST" and lobby["boundary_side"] is None, "Supervisor edits an area (PUT /locations/areas/{id})")
    s, spare = api.call("POST", f"/locations/buildings/{block['id']}/areas",
                        {"name": "Spare", "floor_id": None, "boundary_direction": None, "boundary_name": None, "boundary_side": None}, sup)

    # --- Cameras: Admins and Supervisors ----------------------------------------------------------------
    where = {"building_id": block["id"], "floor_id": ground["id"], "area_id": room["id"]}
    cam_body = {"display_name": "Gate Camera", "connection_type": "RTSP", "stream_url": "rtsp://admin:secret@10.0.0.5:554/s1", "location": where}
    s, refused = api.call("POST", "/cameras", cam_body, sup)
    check(s == 422 and refused.get("code") == "STREAM_CREDENTIALS_NOT_ALLOWED", "stream URL with credentials is refused")
    cam_body["stream_url"] = "rtsp://10.0.0.5:554/s1"
    s, cam = api.call("POST", "/cameras", cam_body, sup)
    check(s == 201 and cam["environment"] == "INDOOR" and cam["location"]["area_name"] == "Server Room",
          "Supervisor adds a camera with a location (POST /cameras {display_name, connection_type, stream_url, location})")
    s, onvif = api.call("POST", "/cameras", {"display_name": "Fence Camera", "connection_type": "ONVIF",
                                             "stream_url": "http://10.0.0.6/onvif", "location": {"building_id": fence["id"], "floor_id": None, "area_id": gate["id"]}}, a.admin)
    check(s == 201 and onvif["environment"] == "OUTDOOR", "Admin adds an ONVIF camera on the perimeter (environment OUTDOOR)")
    s, bare = api.call("POST", "/cameras", {"display_name": "Spare Camera", "connection_type": "RTSP", "stream_url": None, "location": None}, sup)
    check(s == 201 and bare["location"] is None and bare["environment"] is None, "camera without URL or location")
    s, dup = api.call("POST", "/cameras", {"display_name": "Gate Camera", "connection_type": "RTSP", "stream_url": None, "location": None}, sup)
    check(s == 409 and dup.get("code") == "CAMERA_NAME_TAKEN", "duplicate camera name refused (409)")

    s, found = api.call("GET", "/cameras?q=gate", token=sup)
    check(s == 200 and {c["id"] for c in found} == {cam["id"]}, "search by name (GET /cameras?q=)")
    s, found = api.call("GET", f"/cameras?q={cam['id'][:8]}", token=sup)
    check(s == 200 and [c["id"] for c in found] == [cam["id"]], "search by Camera ID")
    s, found = api.call("GET", "/cameras?q=10.0.0.6", token=sup)
    check(s == 200 and [c["id"] for c in found] == [onvif["id"]], "search by stream address")
    s, found = api.call("GET", f"/cameras?building_id={block['id']}&floor_id={ground['id']}", token=a.admin)
    check(s == 200 and [c["id"] for c in found] == [cam["id"]], "filter by building and floor (page URL)")

    s, tree = api.call("GET", "/locations", token=sup)
    b = next(x for x in tree if x["id"] == block["id"])
    check(s == 200 and b["camera_count"] == 1 and b["floors"][0]["areas"][0]["camera_count"] == 1
          and b["floors"][0]["areas"][0]["boundary_direction"] == "WEST", "tree has counts and boundaries (GET /locations)")

    s, _ = api.call("PUT", f"/locations/guard-assignments/{guard['guard_id']}", where, sup)
    s, linked = api.call("GET", f"/locations/areas/{room['id']}/linked", token=sup)
    check(s == 200 and [c["camera_id"] for c in linked["cameras"]] == [cam["id"]]
          and [g["guard_id"] for g in linked["guards"]] == [guard["guard_id"]], "linked cameras and Guards (GET /locations/areas/{id}/linked)")
    s, linked = api.call("GET", f"/locations/buildings/{block['id']}/linked", token=a.admin)
    check(s == 200 and len(linked["cameras"]) == 1, "linked at or below a building")

    s, moved = api.call("PUT", f"/cameras/{bare['id']}", {"display_name": "Spare Camera 2", "connection_type": "ONVIF",
                                                          "stream_url": "https://cam.example.org/onvif", "location": {"building_id": block["id"], "floor_id": None, "area_id": None}}, a.admin)
    check(s == 200 and moved["location"]["level"] == "BUILDING", "edit camera and set a building location (PUT /cameras/{id})")
    s, moved = api.call("PUT", f"/cameras/{bare['id']}", {"display_name": "Spare Camera 2", "connection_type": "ONVIF",
                                                          "stream_url": "https://cam.example.org/onvif", "location": None}, sup)
    check(s == 200 and moved["location"] is None, "location: null removes the location")

    # --- Removal ------------------------------------------------------------------------------------------
    s, busy = api.call("DELETE", f"/locations/areas/{room['id']}", token=sup)
    check(s == 409 and busy.get("code") == "LOCATION_NOT_EMPTY", "an area with a camera and a Guard cannot be removed (409)")
    s, _ = api.call("DELETE", f"/locations/areas/{spare['id']}", token=sup)
    check(s == 204, "Supervisor removes an empty area (DELETE /locations/areas/{id})")
    detect(args.web, args.token, onvif["id"], "WEAPON")
    s, has_alerts = api.call("DELETE", f"/cameras/{onvif['id']}", token=a.admin)
    check(s == 409 and has_alerts.get("code") == "CAMERA_HAS_ALERTS", "a camera with alerts cannot be removed (409)")
    s, _ = api.call("DELETE", f"/cameras/{bare['id']}", token=sup)
    check(s == 204, "Supervisor removes a camera (DELETE /cameras/{id})")
    s, f2 = api.call("POST", f"/locations/buildings/{block['id']}/floors", {"name": "Roof", "level": 5}, a.admin)
    s, _ = api.call("DELETE", f"/locations/floors/{f2['id']}", token=sup)
    check(s == 204, "Supervisor removes an empty floor (DELETE /locations/floors/{id})")
    s, extra = api.call("POST", "/locations/buildings", {"name": "Temp Store", "kind": "INDOOR"}, a.admin)
    s, _ = api.call("DELETE", f"/locations/buildings/{extra['id']}", token=a.admin)
    check(s == 204, "Admin removes an empty building (DELETE /locations/buildings/{id})")

    # --- Guards and other organizations see nothing ---------------------------------------------------------
    g_token = a.guard_sign_in(guard["guard_id"], g_email, "STATIONED")  # the Guard has a post (D-018)
    check(api.call("GET", "/locations", token=g_token)[0] == 403, "a Guard cannot open the location tree")
    check(api.call("GET", "/cameras", token=g_token)[0] == 403, "a Guard cannot list cameras")
    o = Org(api, args.outbox, "Site Other")
    check(api.call("GET", "/cameras", token=o.admin)[1] == [], "another organization sees no cameras")
    check(api.call("GET", "/locations", token=o.admin)[1] == [], "… and no locations")
    check(api.call("PUT", f"/cameras/{cam['id']}", {"display_name": "X", "connection_type": "RTSP", "stream_url": None, "location": None}, o.admin)[0] == 404,
          "… cannot edit this camera (404)")
    check(api.call("DELETE", f"/locations/areas/{room['id']}", token=o.admin)[0] == 404, "… nor remove this area (404)")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print(json.dumps({"organization_name": a.name, "organization_id": a.org_id, "admin_email": a.admin_email,
                      "supervisor_email": sup_email, "password": PASSWORD}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
