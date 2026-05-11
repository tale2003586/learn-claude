import json
import threading
import time
import uuid

from bus.team_bus import BUS
class ProtocolManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.shutdown_requests = {}  # request_id -> {name, timestamp, status}
        self.plan_requests = {}  # request_id -> {name, plan, timestamp, status}

    def handle_shutdown_request(self, name: str) -> str:
        req_id = uuid.uuid4().hex[:8]
        with self._lock:
            self.shutdown_requests[req_id] = {"target": name, "timestamp": time.time(), "status": "pending"}
        BUS.send("lead", name, f"Please shutdown gracefully.", "shutdown_request", {"request_id": req_id})
        return f"Shutdown request sent for {name} with request_id {req_id}"


    def handle_plan_review(self, request_id: str, approved: bool, feedback: str = "") -> str:
        with self._lock:
            req = self.plan_requests.get(request_id)
            if not req:
                return f"Error: No such plan review request {request_id}"
            req["status"] = "approved" if approved else "rejected"

        BUS.send(
        "lead", req["sender"], feedback, "plan_approval_response",
        {"request_id": request_id, "approve": approved, "feedback": feedback},
        )
        return f"Plan {req['status']} for '{req['sender']}'"
    
    def _check_shutdown_status(self, request_id: str) -> str:
        with self._lock:
            return json.dumps(self.shutdown_requests.get(request_id, {"error": "not found"}))
        
    def handle_shutdown_response(self, sender: str, request_id: str, approved: bool, details: str = "") -> str:
        with self._lock:
            req = self.shutdown_requests.get(request_id)
            if not req:
                return f"Error: No such shutdown request {request_id}"
        req["status"] = "approved" if approved else "failed"
        req["details"] = details
        BUS.send(
            sender, "lead", f"reason：{details}", 
            "shutdown_response", {"request_id": request_id, "approve": approved},
        )
        return f"Approved Shutdown response recorded for request_id {request_id}" if approved else f"Shutdown failed for request_id {request_id} with reason: {details}"
    
    def handle_plan_request(self, sender: str, plan: str) -> str:
        request_id = uuid.uuid4().hex[:8]
        with self._lock:
            self.plan_requests[request_id] = {
                "sender": sender,
                "plan": plan,
                "timestamp": time.time(),
                "status": "pending",
            }
        BUS.send(
            sender, "lead", plan, "plan_approval_request",
            {"request_id": request_id, "plan": plan},
        )
        return f"Plan review request {request_id} sent to lead."
    

PROTOCOLS = ProtocolManager()
