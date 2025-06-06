import os
import hendler

def run():
    # Specify the relative directory path
    directory_path = './csvs'  # or '../your_directory' for going one level up

    # Get a list of all files in the directory
    files = [f for f in os.listdir(directory_path) if os.path.isfile(os.path.join(directory_path, f))]

    # Print the file names
    for file in files:
        hendler.insetdata(file)
        print(file)