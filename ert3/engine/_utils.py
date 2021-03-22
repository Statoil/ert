import yaml

from ert3.stats import Gaussian, Uniform


# TODO: This entire file should be replaced by a pydantic schema!
# This is a hack, but at least it lives in isolation, with a plan to get
# rid of it...


def load_parameters(workspace):
    with open(workspace / "parameters.yml") as f:
        parameter_config = yaml.safe_load(f)

    parameters = {}
    for parameter_group in parameter_config:
        dist_config = parameter_group["distribution"]
        if dist_config["type"] == "gaussian":
            distribution = Gaussian(
                dist_config["input"]["mean"],
                dist_config["input"]["std"],
                index=parameter_group["variables"],
            )
        elif dist_config["type"] == "uniform":
            distribution = Uniform(
                dist_config["input"]["lower_bound"],
                dist_config["input"]["upper_bound"],
                index=parameter_group["variables"],
            )
        else:
            raise ValueError(
                "Unknown distribution type: {}".format(dist_config["type"])
            )

        parameters[parameter_group["name"]] = distribution

    return parameters
