<?php
include_once 'db.php';
include 'display.php';


echo "<h2>Display Crew Information</h2>";

if ($_SERVER["REQUEST_METHOD"] == "POST") {
    $flight_num = $_POST["flight_num"];
    $query = "SELECT flight_assignment, name, job FROM crew WHERE flight_assignment = '$flight_num';";
    display($query);
include 'about_crew_form.php'; 
}
?>