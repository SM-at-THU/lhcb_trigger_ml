#lhcb_trigger_ml
LHCb trigger based on machine learning research

Project should enable one to compare several classifiers by their characteristics based on some training dataset.

The programming language is python,
the analysis is performed in IPython notebooks - commonly used in machine learning interactve shell for python, which is good for development, analysis and presenting results (plots, histograms and so on)

At this moment project contains several notebooks which use python classifiers, 
see IPythonWorkflow/ROOTSimpleSkLearn.ipynb for example and explanations.


###Getting this to work
To run the notebooks on some machine, you should have
* CERN ROOT, make sure you have it by typing 'root' in the console
* IPython, see ipython.org/install.html
* Some python libraries that can be installed using any package manager for python
  (apt-get will work too, but Ubuntu repo contains quite old versions of libraries),
  better use pip: 
  pip-installer.org 
  
The libraries you need are
* numpy 
* scipy
* pandas
* scikit-learn 
* rootpy  
* root-numpy

and maybe something else, basically the packages are installed via command-line:
    sudo pip install numpy scipy pandas scikit-learn rootpy root-numpy
IPython can be installed via pip as well
    sudo pip install ipython

###Roadway:
Support for TMVA classifiers will be added soon