**********************
Synchronizer for Linux
**********************

Synchronizer for Linux is a python3 application that allows you to keep multiple pairs of folders synchronized. Events reported on source folders are replicated on target folder.

.. image:: https://user-images.githubusercontent.com/32344641/194986551-d8ecc14e-9cbf-4dca-89c9-3a0ec0a88b6f.png

Events reported
---------------

* File or folder creation
* File or folder movement
* File or folder deletion
* File edition

Features
--------

* Written on python3 and the user interface is built using GTK bindings for python.

* AppIndicator is shown on the system tray to be able to close the main window without exiting the application and to manage the paired folders.

* Multiple pairs of folders can be configured.

* Configuration can be saved to configuration file to make it persistent.

Usage
-----

From source
'''''''''''

.. code:: bash

   git clone https://github.com/ahsand97/Synchronizer-for-Linux.git
   cd Synchronizer-for-Linux
   python3 -m pip install -r requirements.txt
   python3 synchronizer.py

AppImage
''''''''

An AppImage is provided to use the application. You can download it from the `releases <https://github.com/ahsand97/Synchronizer-for-Linux/releases>`_.

----------------------------------------------------------------------------------------------------------------------------------------------------------------

**The argument** ``--hidden`` **can be passed to the application to start it silently (no errors messages shown on its startup) and also the main window will be hidden, only the AppIndicator would be visible.**

**The environment variable** ``MONOSPACED_FONT`` **can be passed to the application to change the default monospaced font.**
