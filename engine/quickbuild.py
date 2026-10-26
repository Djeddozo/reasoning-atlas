"""An explicit build request goes straight to the existing dev loop."""
import json
import re
import threading

from . import claudeauth, db, devloop, flow

_lock = threading.Lock()


def start(con, text, request_id):
    text = text.strip()
    if not text or len(text) > 20000:
        raise ValueError('Describe what to build (1–20,000 characters).')
    if not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
        raise ValueError('Invalid build request. Reload and try again.')
    with _lock:
        previous = con.execute('SELECT task_id, result FROM quick_build_requests WHERE request_id = ?', (request_id,)).fetchone()
        if previous:
            return json.loads(previous['result'])
        auth = claudeauth.check()
        if auth['state'] != 'connected':
            raise ValueError('Reconnect Claude under System → Claude connection before building.')
        task_id = db.insert(con, 'tasks', title=text.splitlines()[0][:180], description=text,
                            owner='ai', status='todo')
        # Reserve before launching so retries cannot start the same request twice.
        result = {'task_id': task_id, 'started': False,
                  'message': f'Task #{task_id} saved. Check its run status before retrying.'}
        con.execute('INSERT INTO quick_build_requests VALUES (?, ?, ?)', (request_id, task_id, json.dumps(result)))
        con.commit()
        flow.run(con, task_id)
        reason = devloop.why_not_now(con, task_id)
        if reason:
            result['message'] = f'Task #{task_id} is first in your queue. Not started: {reason}'
        else:
            try:
                message = devloop.start_background(task_id=task_id)
                result.update(started=True, message=f'Task #{task_id}: {message}')
            except OSError:
                result['message'] = f'Task #{task_id} saved, but the run could not start. Retry from the task list.'
        con.execute('UPDATE quick_build_requests SET result = ? WHERE request_id = ?', (json.dumps(result), request_id))
        db.log(con, 'me', 'quick_build', f'task #{task_id}; started={result["started"]}')
        con.commit()
        return result
