<?php // db.php 

$host = "pluto.hood.edu"; 
$dbname = "ajo2db";
$user = "ajo2"; 
$pass = "password"; 

try { 
	$conn = new PDO("mysql:host=$host;dbname=$dbname", $user, $pass); 
	$conn->setAttribute( PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION ); 
}
catch(PDOException $e) { 
	die("Could not connect to the database $dbname :" . $e->getMessage()); 
}
?>
