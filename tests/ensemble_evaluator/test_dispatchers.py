from tests.narratives import dispatchers_failing_job_narrative


def test_dispatchers_with_failing_job(unused_tcp_port):
    with dispatchers_failing_job_narrative.on_uri(
        f"ws://localhost:{unused_tcp_port}/dispatch"
    ):
        pass
