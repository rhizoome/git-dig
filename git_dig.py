import click


@click.group()
def main():
    """Click entrypoint."""
    pass


@main.command()
def run():
    print("huhu")
