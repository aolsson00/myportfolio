<?php 
include_once 'db.php'; 
include 'display.php'; 

echo "<h2>Display Flight Information</h2>"; 
display("SELECT * FROM flight;"); 
?> 

<br/> 
<form action="display_crew.php" method="post"> 
<h2>Name to Select: </h2> 
Flight Number: <input type="text" name="flight_num"/><br> 
<input type="submit" value="Select"/><input type="reset"/> 
</form>
