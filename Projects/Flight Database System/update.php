<?php 

include_once 'db.php'; 

# form data 
$passenger_id = $_POST['passenger_id']; 
$address = $_POST['address']; 

$query = "UPDATE passenger_address SET address = :address WHERE passenger_id = :passenger_id;"; 
$data = array('address' => $address, 'passenger_id' => $passenger_id); 
$stmt = $conn->prepare($query); 

if ($stmt->execute($data)) { 
    $rows_affected = $stmt->rowCount(); 
    echo "<h2>".$rows_affected." Passenger Updated Successfully!</h2>"; 
} else { 
    echo "Update failed: (" . $stmt->errorCode() . ") " . implode(" ", $stmt->errorInfo()); 
}
$conn->close(); 

?>
