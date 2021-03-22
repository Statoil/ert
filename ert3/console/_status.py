import ert3.workspace as ert3_workspace
import ert3.storage as ert3_storage


def status(workspace_root):
    experiments = ert3_workspace.get_experiment_names(workspace_root)
    done = ert3_storage.get_experiment_names(workspace=workspace_root)
    pending = [experiment for experiment in experiments if experiment not in done]

    if done:
        print("Experiments that have run already:")
        for experiment in done:
            print(f"  {experiment}")

    if pending:
        if done:
            print()
        print("Experiments that can be run:")
        for experiment in pending:
            print(f"  {experiment}")

    if not done and not pending:
        print("No experiments present in this workspace")
