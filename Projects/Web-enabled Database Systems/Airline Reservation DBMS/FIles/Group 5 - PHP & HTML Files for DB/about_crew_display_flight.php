<?php 
include_once 'db.php'; 
include 'display.php'; 

echo "<h2>Display Flight Information</h2>"; 
display("SELECT * FROM flight ;"); 

?> 