"""Synthetic fixture measurements on an explicitly registered disposable only."""
import statistics
from sqlalchemy import text

from backend.db.readiness import assert_disposable


def summarize(plan):
    fields = ("Node Type", "Index Name", "Actual Rows", "Actual Total Time", "Shared Hit Blocks", "Shared Read Blocks")
    value = {key: plan[key] for key in fields if key in plan}
    if plan.get("Plans"):
        value["Plans"] = [summarize(child) for child in plan["Plans"]]
    return value


def benchmark(engine, url, confirmation, nonce):
    with engine.begin() as connection:
        assert_disposable(connection, url, confirmation, nonce)
        connection.exec_driver_sql("""INSERT INTO auth_users(id,email,password_hash,is_active,created_at)
            SELECT md5('perf-user-'||g), 'fixture-'||g||'@example.invalid', 'unusable',true,now() FROM generate_series(1,100) g""")
        connection.exec_driver_sql("""INSERT INTO chat_sessions(id,user_id,title,created_at,updated_at)
            SELECT md5('perf-chat-'||g),md5('perf-user-'||(mod(g-1,100)+1)), 'Synthetic',now(),now()-g*interval '1 second'
            FROM generate_series(1,5000) g""")
        connection.exec_driver_sql("""INSERT INTO chat_messages(id,session_id,role,content,sources_json,position,created_at)
            SELECT md5('perf-message-'||g),md5('perf-chat-'||((g-1)/20+1)), 'user','Synthetic fixture','[]',mod(g-1,20)+1,now()
            FROM generate_series(1,100000) g""")
        connection.exec_driver_sql("""INSERT INTO ai_idempotency(id,user_id,session_id,request_key,request_hash,status,response_json,
            rate_charged,provider_attempts,created_at,updated_at)
            SELECT md5('perf-request-'||g),md5('perf-user-'||(mod(g-1,100)+1)),md5('perf-chat-'||g),
            md5('perf-key-'||g)::uuid::text,repeat('0',64),'completed','{}',true,2,now(),now()
            FROM generate_series(1,5000) g""")
        connection.exec_driver_sql("""INSERT INTO chat_generation_leases(session_id,owner_id,request_id,version,expires_at,updated_at)
            SELECT md5('perf-chat-'||g),md5('perf-owner-'||g),md5('perf-request-'||g),1,now()-g*interval '1 minute',now()
            FROM generate_series(1,5000) g""")
        connection.exec_driver_sql("""INSERT INTO ai_rate_limits(bucket_key,count,created_at)
            SELECT md5('perf-user-'||g)||':1',1,now()-g*interval '1 minute' FROM generate_series(1,10000) g""")
        connection.exec_driver_sql("""INSERT INTO ai_operations(id,request_id,slot,slot_version,generation_version,status,day,month,
            remaining_usd,input_price,output_price,input_limit,planner_limit,output_limit,model,created_at,finished_at)
            VALUES(repeat('a',32),NULL,1,1,1,'completed','d:2026-10-31','m:2026-10',0,1,2,16384,2048,4096,'fixture',now(),now())""")
        # The operation/phase uniqueness requires distinct synthetic operations.
        connection.exec_driver_sql("""INSERT INTO ai_operations(id,request_id,slot,slot_version,generation_version,status,day,month,
            remaining_usd,input_price,output_price,input_limit,planner_limit,output_limit,model,created_at,finished_at)
            SELECT md5('perf-op-'||g),NULL,1,1,1,'completed','d:2026-10-31','m:2026-10',0,1,2,16384,2048,4096,'fixture',now(),now()
            FROM generate_series(1,25000) g""")
        connection.exec_driver_sql("""INSERT INTO ai_provider_attempts(id,operation_id,phase,provider,model,status,input_tokens,output_tokens,
            reserved_usd,estimated_usd,started_at,finished_at)
            SELECT md5('perf-attempt-'||g),md5('perf-op-'||g),'answer','groq','fixture','success',10,10,0.01,0.00003,
            now()-g*interval '1 minute',now() FROM generate_series(1,25000) g""")
        connection.exec_driver_sql("ANALYZE")
        queries = {
            "chat_ownership_order": "SELECT id FROM chat_sessions WHERE user_id=md5('perf-user-1') ORDER BY updated_at DESC,id LIMIT 50",
            "message_history": "SELECT role,content FROM chat_messages WHERE session_id=md5('perf-chat-1') ORDER BY position DESC LIMIT 12",
            "idempotency_key": "SELECT status FROM ai_idempotency WHERE user_id=md5('perf-user-1') AND session_id=md5('perf-chat-1') AND request_key=md5('perf-key-1')::uuid::text",
            "lease_owner": "SELECT version FROM chat_generation_leases WHERE session_id=md5('perf-chat-1')",
            "lease_expiry_backlog": "SELECT count(*) FROM chat_generation_leases WHERE expires_at<now()",
            "rate_cleanup": "SELECT bucket_key FROM ai_rate_limits WHERE created_at<now()-interval '10 minutes' ORDER BY created_at LIMIT 100",
            "usage_24h": "SELECT status,count(*),sum(estimated_usd) FROM ai_provider_attempts WHERE started_at>=now()-interval '24 hours' GROUP BY status",
        }
        results = {}
        for name, sql in queries.items():
            runs = [connection.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)).scalar_one()[0] for _ in range(3)]
            results[name] = {"median_execution_ms": statistics.median(run["Execution Time"] for run in runs),
                             "plan": summarize(runs[-1]["Plan"])}
        return {"server_version_num": int(connection.scalar(text("SHOW server_version_num"))),
                "synthetic": True, "fixtures": {"accounts":100,"chats":5000,"messages":100000,"attempts":25000},
                "indexes_added": [], "queries": results}
