import builtins
from multiprocessing import Process
import sys
import time

import pytest
import torch

import syft
from syft import TorchHook
from syft.workers import WebsocketClientWorker
from syft.workers import WebsocketServerWorker


def _start_proc(participant, dataset: str = None, **kwargs):  # pragma: no cover
    """Helper function for spinning up a websocket participant."""

    def target():
        server = participant(**kwargs)
        if dataset is not None:
            data, key = dataset
            server.add_dataset(data, key=key)
        server.start()

    p = Process(target=target)
    p.start()
    return p


def instantiate_websocket_client_worker(max_tries=5, sleep_time=0.1, **kwargs):  # pragma: no cover
    """Helper function to instantiate the websocket client.

    If a connection is refused, we wait a bit (`sleep_time` seconds) and try again.
    After `max_tries` failed tries, a ConnectionRefusedError is raised.
    """
    retry_counter = 0
    connection_open = False
    while not connection_open:
        try:
            remote_proxy = WebsocketClientWorker(**kwargs)
            connection_open = True
        except ConnectionRefusedError as e:
            if retry_counter < max_tries:
                retry_counter += 1
                time.sleep(sleep_time)
            else:
                raise e
    return remote_proxy


@pytest.fixture()
def start_proc():  # pragma: no cover
    return _start_proc


@pytest.fixture()
def start_remote_worker():  # pragma: no cover
    """Helper function for starting a websocket worker."""

    def _start_remote_worker(
        id, hook, dataset: str = None, host="localhost", port=8768, max_tries=5, sleep_time=0.01
    ):
        kwargs = {"id": id, "host": host, "port": port, "hook": hook}
        server = _start_proc(WebsocketServerWorker, dataset=dataset, **kwargs)
        remote_proxy = instantiate_websocket_client_worker(
            max_tries=max_tries, sleep_time=sleep_time, **kwargs
        )

        return server, remote_proxy

    return _start_remote_worker


@pytest.fixture(scope="session", autouse=True)
def hook():
    hook = TorchHook(torch)
    return hook


@pytest.fixture(scope="function", autouse=True)
def workers(hook):
    # To run a plan locally the local worker can't be a client worker,
    # since it needs to register objects
    # LaRiffle edit: doing this increases the reference count on pointers and
    # breaks the auto garbage collection for pointer of pointers, see #2150
    # hook.local_worker.is_client_worker = False

    # Reset the hook and the local worker
    syft.local_worker.clear_objects()
    syft.frameworks.torch.hook.hook_args.hook_method_args_functions = {}
    syft.frameworks.torch.hook.hook_args.hook_method_response_functions = {}
    syft.frameworks.torch.hook.hook_args.get_tensor_type_functions = {}

    # Define 3 virtual workers
    alice = syft.VirtualWorker(id="alice", hook=hook, is_client_worker=False)
    bob = syft.VirtualWorker(id="bob", hook=hook, is_client_worker=False)
    james = syft.VirtualWorker(id="james", hook=hook, is_client_worker=False)

    workers = {"me": hook.local_worker, "alice": alice, "bob": bob, "james": james}

    yield workers

    alice.remove_worker_from_local_worker_registry()
    bob.remove_worker_from_local_worker_registry()
    james.remove_worker_from_local_worker_registry()


@pytest.fixture
def hide_module():
    import_orig = builtins.__import__
    # When we check for imports in dependency_check, we don't actually attempt
    # to import each package, so popping a module from sys.modules and mocking
    # the import statement is not sufficient to simulate the dependency check
    # for when the dependency is absent. The way we check for dependencies
    # (importlib.util.find_spec) uses module Finders in the sys.meta_path when
    # checking for module specs, so we need to mock the find_spec method of the
    # Finder that will discover the module we want to hide. That Finder happens
    # to be in position three of the meta path.
    find_spec_orig = sys.meta_path[3].find_spec

    def mocked_import(name, globals, locals, fromlist, level):
        if name in ["tensorflow", "tf_encrypted", "torch"]:
            raise ImportError()

        return import_orig(name, globals, locals, fromlist, level)

    def mocked_find_spec(self, fullname, target=None):
        if self in ["tensorflow", "tf_encrypted"]:
            return None
        return find_spec_orig(self, fullname, target)

    builtins.__import__ = mocked_import
    sys.meta_path[3].find_spec = mocked_find_spec
    yield
    builtins.__import__ = import_orig
    sys.meta_path[3].find_spec = find_spec_orig
