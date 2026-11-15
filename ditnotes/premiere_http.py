"""Narrow local HTTP adapter for the deterministic Premiere bridge."""
import json
from urllib.parse import parse_qs, urlsplit
from .premiere_bridge import PremiereBridge


def bridge(server):
    return PremiereBridge(server.project, catalog_provider=server.catalog.read)


def handle(handler, head=False):
    parsed = urlsplit(handler.path)
    if not parsed.path.startswith('/api/premiere/'):
        return False
    try:
        host = handler.headers.get('Host', '')
        if host.split(':', 1)[0].lower() not in ('127.0.0.1', 'localhost'):
            raise PermissionError('Loopback host required')
        api = bridge(handler.server)
        authorization = handler.headers.get('Authorization', '')
        token = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else None
        path = parsed.path.removeprefix('/api/premiere/')
        if handler.command in ('GET', 'HEAD'):
            if path == 'status':
                if authorization:
                    api.authorize(token)
                worker = getattr(handler.server, 'premiere_worker', None)
                result = api.status() | {'journal': worker.status() if worker else None}
            elif path == 'operation':
                if authorization:
                    api.authorize(token)
                query = parse_qs(parsed.query)
                if set(query) != {'operation_id'} or len(query['operation_id']) != 1:
                    raise ValueError('One operation ID is required')
                result = api.operation_status(query['operation_id'][0])
            elif path == 'receipts':
                if authorization:
                    api.authorize(token)
                query = parse_qs(parsed.query)
                if set(query) != {'operation_id'} or len(query['operation_id']) != 1:
                    raise ValueError('One operation ID is required')
                result = {'receipts': api.receipt_history(query['operation_id'][0])}
            elif path == 'request':
                query = parse_qs(parsed.query)
                if set(query) != {'native_project_path'} or len(query['native_project_path']) != 1:
                    raise ValueError('One native project path is required')
                native_path = query['native_project_path'][0]
                config = api.authorize(token)
                if native_path != config['native_project_path']:
                    raise ValueError('Open the paired Premiere project before syncing')
                # Reconcile desired state from durable metadata on every paired
                # poll. This catches offline edits, catalog updates and restarts;
                # an unchanged snapshot does not create another revision.
                api.queue_snapshot()
                result = api.next_operation(token, native_path)
            else:
                handler._json({'error': 'Unknown Premiere operation'}, head, 404)
                return True
        elif handler.command == 'POST':
            origin = handler.headers.get('Origin')
            if origin and origin not in ('http://' + host,):
                raise PermissionError('Local origin required')
            if handler.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise PermissionError('Local origin required')
            if handler.headers.get_content_type() != 'application/json':
                raise ValueError('JSON is required')
            length = int(handler.headers.get('Content-Length', '0'))
            if length <= 0 or length > 4 * 1024 * 1024:
                raise ValueError('Invalid request size')
            value = json.loads(handler.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError('Expected a JSON object')
            if path == 'prepare':
                result = api.prepare(value['native_project_path'])
            elif path == 'queue':
                operation = api.queue_snapshot()
                result = api.status() | {'operation_id': operation['operation_id']}
            elif path in ('reveal', 'selects'):
                operation = api.queue_command(path, value['ranges'], value.get('name'), value.get('request_id'))
                result = api.status() | {'operation_id': operation['operation_id']}
            elif path == 'cancel':
                result = api.cancel(value['operation_id'], value.get('reason', 'Operator cancelled request; retain Premiere changes'))
            elif path == 'retry':
                result = api.retry(value['operation_id'])
            elif path == 'receipt':
                api.authorize(token)
                result = api.acknowledge(value, token)
            else:
                handler._json({'error': 'Unknown Premiere operation'}, head, 404)
                return True
        else:
            raise ValueError('Unsupported request method')
        handler._json(result, head)
    except PermissionError as error:
        handler._json({'error': str(error)}, head, 403)
    except (ValueError, KeyError, TypeError, OSError) as error:
        handler._json({'error': str(error)}, head, 400)
    return True
